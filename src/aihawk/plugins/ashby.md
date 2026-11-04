---
name: ashby
hosts: [jobs.ashbyhq.com]
apply_path: /application
---
The form is one page, with two file inputs: the first is "Autofill from resume", the second is the real field, `#_systemfield_resume`. Upload the CV to `#_systemfield_resume`. Do not use the autofill input.

`#_systemfield_name` and `#_systemfield_email` are the name and the email. Other fields have long generated ids: find each one by the label just before it.

Yes and No questions, such as work authorization or visa sponsorship, are answered by clicking a button labelled Yes or No under the question. Selectors for those buttons must be specific to that question. After clicking, read the page to check that the answer is selected.

The submit control is the button "Submit Application".
