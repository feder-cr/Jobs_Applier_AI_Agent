---
name: ashby
hosts: [jobs.ashbyhq.com]
apply_path: /application
---
The form is on the posting's Application tab (`#job-application-form`), at `/application`. It is one page, with two file inputs: the first is "Autofill from resume", the second is the real field, `#_systemfield_resume`. Upload the CV to `#_systemfield_resume`. Do not use the autofill input.

`#_systemfield_name` and `#_systemfield_email` are the name and the email. Other fields have long generated ids: find each one by the label just before it. The phone is labelled "Primary Phone Number" and has the placeholder "1-415-555-1234".

Location is a typeahead with the placeholder "Start typing...".

Yes and No questions, such as work authorization or visa sponsorship, are answered by clicking a button labelled "Yes" or "No", and each pair follows its question's label. Those buttons carry generated ids, so read the question's own container with browser_read_html and take the button from inside it: a selector borrowed from another question answers the wrong one. Read the page afterwards to see the answer selected, and if a required question will not take one, call finish_form with status blocked and name the question.

The submit control is the button "Submit Application". On two forms in the trials it stayed disabled and the click did not land, so nothing was sent: read the page for a required field still empty, fill it, and read the page again. If it stays disabled, call finish_form with status blocked and name the fields the page marks as missing. Why it stays disabled on a form that looks complete is not known.

After sending: a real send showed "Your application was successfully submitted. We'll contact you if there are next steps!". Read the page: if the form is still there, the application was not received.
