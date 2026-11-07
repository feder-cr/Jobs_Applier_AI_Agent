---
name: recruitee
hosts: [recruitee.com]
apply_path: /c/new
---
The form is on the posting's Application tab (Sollicitatie or Application), at /c/new. The page is often in Dutch or German. A cookie banner may cover the page: click "Agree to necessary" first. A Cookiebot banner may show instead, with Dutch labels: "Weigeren" declines.

Fields are named `candidate.name`, `candidate.email` and `candidate.phone`. Their ids change on each load, so select by name, such as `input[name="candidate.email"]`. The phone has a country code list in a button just before it, whose label names the country: set it to the country of the number in the answers. That button is a menu, so if it will not open or take a choice, type the number whole with its + code instead and leave the button alone. A number typed whole with its + code is taken too: the list then reads "International" and keeps the number.

Upload the CV to `input[name="candidate.cv"]`. `candidate.photo` is a photo and `candidate.coverLetterFile` a cover letter: leave them empty unless they are required. The cover letter has a switch button, "Write it here instead" (in Dutch, "Schrijf je motivatiebrief in plaats daarvan hier"): do not click it.

Questions a company adds come under "Questions" (Vragen). Some forms ask them as a dropdown, a radio list or a group of boxes, and some have no agreement box at all. Yes and No questions are radio buttons named `candidate.openQuestionAnswers.<id>.flag`; a written answer is `candidate.openQuestionAnswers.<id>.content`. Answer in the company's language.

`candidate.agreements.0.consent` is a checkbox under "Legal Agreements", worded "Yes, I give my consent for <company> to hold my details for GDPR compliance".

A required field has a * with the title "This field is required and can not be left empty".

The submit control has `data-testid="submit-application-form-button"`, labelled "Send", "Versturen" or "Senden".

After sending: the page says "Fertig!" and "Danke für deine Bewerbung!" on a German form, at an address ending in `/applied`. Read the page: if the form is still there, the application was not received.
