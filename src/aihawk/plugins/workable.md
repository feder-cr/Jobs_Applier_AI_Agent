---
name: workable
hosts: [apply.workable.com]
apply_path: /apply
---
The form loads a moment after the page: if it looks empty, read the page again. A cookie banner may show, `div[aria-label='Cookie Consent']`, with "Accept all" and "Decline all" buttons: click "Decline all" by its text inside that banner. A click by position (`button:nth-of-type(1)`) is refused while the banner is up, as the event lands on the banner: seen on a real form, 16 seconds lost.

The sections are headed "Personal information", "Profile", "Resume", "Details" and "Cover letter", and some have "(Optional)" after the title.

`#firstname`, `#lastname`, `#email` and `#input_phone` are the basics, and the phone has a country button before it: choose the country there and type the national number. An "invalid phone" error that shows while typing can clear when the number is typed again whole. Some forms also ask for an address: `#address`, `#city`, `#postcode` and `#country`.

Upload the CV under the "Resume" heading. On a real form the file input there, `#input_files_input_<id>`, refused browser_upload_files (the click landed on an icon), and the input `#description_input_<id>` in the same section took the file, which then showed by name beside it: try `input[type="file"]` in that section first, and that second id when the first is refused. Do not use "Autofill application" or "Import resume from".

"Experience" and "Education" with a "+ Add" button are usually optional: leave them empty unless required.

Questions a company adds have names like `CA_30078` or `QA_11839078`: find each by its label. A required "Work Authorization" list is common, with options that depend on the job's country. A field showing "Select an option…" is a list: type, then click the option; its input has an id like `#input_CA_30080_input`.

Radio answers have generated ids. Click the radio's box by what it holds: `div[role="radio"]:has(input[name="QA_12536970"][value="true"])` for Yes and `[value="false"]` for No, which worked for seven questions in a row on a real form; then check the result by reading the page.

A `gdpr` checkbox states that the person read the privacy notice and consents to the processing of their data: tick it with `div[role="checkbox"]:has(input[name="gdpr"])`.

The submit control is `form > button[type='submit']`, the button "Submit application" (in Portuguese, "Enviar candidatura").

A Cloudflare box "verifying you are not a robot" can sit above the submit button. Seen on a real form with the button stuck on "Submitting…" beside the box, unticked: if it shows before sending, tick it first.

After sending: read the page: if the form is still there, the application was not received.
