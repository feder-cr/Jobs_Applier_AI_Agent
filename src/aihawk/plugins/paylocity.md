---
name: paylocity
hosts: [recruiting.paylocity.com]
---
If the page shows a job's details rather than a form, follow its Apply link.

The form has several steps ("Step 1 of 3"): fill each, then go on with its next button, and read the page again. Report ready only on the last step, with the control that sends the form.

Upload the CV under "Select Resume to Upload". Leave "Fill out application with my resume" unticked: you fill the fields.

Fields have ids such as `#info\.firstName`, `#info\.lastName`, `#info\.email`, `#info\.cellPhone`. Escape the dot in the selector. The address uses `#public-site-address-address-1`, `-city`, `-county`, `-zip`, and a state list at `#public-site-address-us-state`. These forms often require a full home address, work history and references: what the files do not say goes back to the person.

Demographic questions on the last steps are voluntary: choose the option that declines to answer.
