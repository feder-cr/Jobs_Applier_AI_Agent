---
name: smartrecruiters
hosts: [jobs.smartrecruiters.com]
---
The job page shows the posting and an "I'm interested" link. Follow it: the form opens as "Easy Apply" on an oneclick-ui page.

The form is in steps. Fill the fields of the step you see, go on with the footer "Next" button, and read the page again. Report ready only on the last step, with the control that sends the form.

Do not use "Apply With Indeed" or "Apply with LinkedIn": they fill the form from another account. Upload the CV with the file input inside `data-test="resume-upload"`. Leave the one inside `apply-with-resume-container` alone.

Name and email: `#first-name-input`, `#last-name-input`, `#email-input`, `#confirm-email-input` where present. The phone is a combobox labelled "Country code" next to `input[type=tel]` labelled "Phone number". Ids such as `spl-form-element_5` are generated: find fields by their label.
