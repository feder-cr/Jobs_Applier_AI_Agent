---
name: jazzhr
hosts: [applytojob.com]
---
The posting page shows the job and the application form below it. Click "Apply Now" (`a[href="#"]`) only if the form is not visible; the URL does not change and the form opens on the same page.

The basics are `#resumator-firstname-value`, `#resumator-lastname-value`, `#resumator-email-value`, `#resumator-phone-value`, and the address fields `#resumator-address-value`, `#resumator-city-value`, `#resumator-state-value`, `#resumator-postal-value`. Phone is plain text, no country picker.

The resume is attached with the link `#resumator-choose-upload` ("Attach resume"); read the page to find the file input behind it. Do not use "Paste resume".

Questions the company adds are native selects `select[name^="resumator-questionnaire"]`, default "-- No answer --" (value `resumator_no_selection`), or yes/no selects with value "0". Choose the option by its visible label and read back the value. Free questions are text inputs or textareas with the same name prefix. EEO radio groups (`resumator-eeoc_*-value`) and selects (`resumator-eeo_*-value`) are optional; do not guess them.

The send control is `#resumator-submit-resume` (`input[type=button]`, value "Submit Application"). It is not a real submit button: it is driven by script. The page carries a reCAPTCHA iframe; do not try to solve it.

After sending: read the page: if the form is still there, the application was not received.
