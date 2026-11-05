---
name: recruitee
hosts: [recruitee.com]
apply_path: /c/new
---
The form is on the posting's Application tab (Sollicitatie or Application), at /c/new. The page is often in Dutch. A cookie banner may cover the page: click "Agree to necessary" first. A Cookiebot banner may show instead, with Dutch labels: "Weigeren" declines.

Fields are named `candidate.name`, `candidate.email` and `candidate.phone`. Their ids change on each load, so select by name, such as `input[name="candidate.email"]`. The phone has a country code list in a button just before it, whose label names the country: set it to the country of the number in the answers.

Upload the CV to `input[name="candidate.cv"]`. `candidate.photo` is a photo and `candidate.coverLetterFile` a cover letter: leave them empty unless they are required, and if a required one is not among your files, ask. The cover letter has a switch button, "Write it here instead" (in Dutch, "Schrijf je motivatiebrief in plaats daarvan hier"): do not click it.

Questions a company adds come under "Questions" (Vragen). Yes and No questions are radio buttons named `candidate.openQuestionAnswers.<id>.flag`; a written answer is `candidate.openQuestionAnswers.<id>.content`. Answer in the company's language only where the answers file gives the words; a required answer it does not give is a question for the person.

`candidate.agreements.0.consent` is a checkbox under "Legal Agreements", worded "Yes, I give my consent for <company> to hold my details for GDPR compliance". Tick it only if the answers file says the person accepts that.

A required field has a * with the title "This field is required and can not be left empty".

The submit control has `data-testid="submit-application-form-button"`, labelled "Send" or "Versturen".

After sending: the page says "Fertig!" and "Danke für deine Bewerbung!" on a German form, at an address ending in `/applied`. Recruitee usually sends an auto-confirmation email only for applications made on the careers site, not for applications by email or added by hand. Its wording is written per job. Read the page: if the form is still there, the application was not received.
