---
name: ashby
hosts: [jobs.ashbyhq.com]
apply_path: /application
---
The form is on the posting's Application tab (`#job-application-form`), at /application. It is one page, with two file inputs: the first is "Autofill from resume", the second is the real field, `#_systemfield_resume`. Upload the CV to `#_systemfield_resume`. Do not use the autofill input.

`#_systemfield_name` and `#_systemfield_email` are the name and the email. Other fields have long generated ids: find each one by the label just before it. The phone is labelled "Primary Phone Number" and has the placeholder "1-415-555-1234".

Location is a combobox with the placeholder "Start typing...": type, then pick the suggestion.

Yes and No questions, such as work authorization or visa sponsorship, are answered by clicking a button labelled Yes or No. Each Yes and No pair follows its question's label. Selectors for those buttons must be specific to that question. After clicking, read the page to check that the answer is selected.

The submit control is the button "Submit Application".

After sending: a real send showed "Your application was successfully submitted. We'll contact you if there are next steps!". Read the page: if the form is still there, the application was not received. On two forms in the trials the Submit Application button stayed disabled and the click did not land, so nothing was sent.
