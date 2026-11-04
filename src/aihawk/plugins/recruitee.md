---
name: recruitee
hosts: [recruitee.com]
apply_path: /c/new
---
A cookie banner may cover the page: click "Agree to necessary" first.

Fields are named `candidate.name`, `candidate.email` and `candidate.phone`. Their ids change on each load, so select by name, such as `input[name="candidate.email"]`. The phone has a country code list in a button just before it: set it to the country of the number in the answers.

Upload the CV to `input[name="candidate.cv"]`. `candidate.photo` is a photo and `candidate.coverLetterFile` a cover letter: leave them empty unless they are required, and if a required one is not among your files, ask.

Questions a company adds come under "Questions". The form is often in the company's own language: answer in that language only where the answers file gives the words; a required answer it does not give is a question for the person.
