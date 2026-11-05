---
name: workable
hosts: [apply.workable.com]
apply_path: /apply
---
The form loads a moment after the page: if it looks empty, read the page again. A cookie banner may show, with "Accept all" and "Decline all" buttons.

The sections are headed "Personal information", "Profile", "Resume", "Details" and "Cover letter", and some have "(Optional)" after the title.

`#firstname`, `#lastname`, `#email` and `#input_phone` are the basics, and the phone has a country button before it: choose the country there and type the national number. An "invalid phone" error that shows while typing can clear when the number is typed again whole. Some forms also ask for an address: `#address`, `#city`, `#postcode` and `#country`.

Upload the CV to the file input under the "Resume" heading, which has an id like `#input_files_input_...`: select it with `input[type="file"]` in that section. Do not use "Autofill application" or "Import resume from".

"Experience" and "Education" with a "+ Add" button are usually optional: leave them empty unless required.

Questions a company adds have names like `CA_30078` or `QA_11839078`: find each by its label. A required "Work Authorization" list is common, with options that depend on the job's country. A field showing "Select an option…" is a list: type, then click the option; its input has an id like `#input_CA_30080_input`.

Radio answers have generated ids. Select them by `input[name="CA_41201"]` with the right position, or by the `div[role="radio"]` beside it that shows YES or NO, and check the result by reading the page.

A `gdpr` checkbox states that the person read the privacy notice and consents to the processing of their data.

The submit control is `form > button[type='submit']`, the button "Submit application" (in Portuguese, "Enviar candidatura").

A Cloudflare box "verifying you are not a robot" can sit above the submit button. Seen on a real form with the button stuck on "Submitting…" beside the box, unticked: if it shows before sending, tick it first.

After sending: read the page: if the form is still there, the application was not received.
