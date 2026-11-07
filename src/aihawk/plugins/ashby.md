---
name: ashby
hosts: [jobs.ashbyhq.com]
apply_path: /application
---
The form is on the posting's Application tab (`#job-application-form`), at `/application`. It is one page, with two file inputs: the first is "Autofill from resume", the second is the real field, `#_systemfield_resume`. Upload the CV to `#_systemfield_resume`. Do not use the autofill input.

`#_systemfield_name` and `#_systemfield_email` are the name and the email, and `#_systemfield_location` the location. Every other field has a generated id, a bare uuid such as `#ab317b70-8cd9-49f7-9d90-25dc8837a521`, so find each one by the label just before it and take the id from that label's `for`. The phone is labelled "Primary Phone Number" and has the placeholder "1-415-555-1234".

Location is a typeahead with the placeholder "Start typing...".

Yes and No questions, such as work authorization or visa sponsorship, are answered by clicking a button labelled "Yes" or "No", and each pair follows its question's label. Those buttons carry generated ids, so take each one from its own question's container: on a real form `div:has(> label[for="<question uuid>"]) button:nth-of-type(2)` was No and the radio answers were reached by their own `label[for="<question uuid>_<option uuid>-labeled-..."]`. Read the page afterwards to see the answer selected.

A consent checkbox `#<uuid>__systemfield_data_consent_ack-labeled-checkbox` is required where the form has one.

The submit control is the button "Submit Application", which no id names: on a real form `#form > button:last-of-type` matched it alone. On two forms in the trials it stayed disabled and the click did not land, so nothing was sent: read the page for a required field still empty, fill it, and read the page again. If it stays disabled, call finish_form with status blocked and name the fields the page marks as missing. Why it stays disabled on a form that looks complete is not known.

After sending: a real send showed "Your application was successfully submitted. We'll contact you if there are next steps!". Read the page: if the form is still there, the application was not received.
