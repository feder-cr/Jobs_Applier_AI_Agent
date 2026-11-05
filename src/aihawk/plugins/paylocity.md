---
name: paylocity
hosts: [recruiting.paylocity.com]
---
If the page shows a job's details rather than a form, follow its Apply link. A closed posting goes to "Job Not Found" (/Recruiting/Jobs/JobNotFound): report the form as blocked.

The form has several steps ("Step 1 of 3" or "Step 1 of 5"): fill each, then go on with its next button, "Next Step" (`#btn-submit`), and read the page again. Report ready only on the last step, with the control that sends the form. These forms are long: when a step needs what the files do not say, report needs_input with all of that step's questions at once, rather than going field by field.

Upload the CV under "Select Resume to Upload". "Fill out application with my resume" (`#useAttachedResumeToFillOutApplication`) is ticked when the page opens: untick it, since you fill the fields. The site's "Apply with resume" dialog can still fill the email, the mobile number and the address from the file: read them afterwards and correct what is wrong.

Fields have ids such as `#info\.firstName`, `#info\.lastName`, `#info\.email`, `#info\.cellPhone` and `#info\.phone`. Escape the dot in the selector. The address uses `#public-site-address-address-1` (a combobox: read the page after typing), `-city`, `-county`, `-zip`, and a state list at `#public-site-address-us-state`. The mobile number is written with no spaces, such as `+447700900123`: with spaces it is refused as "Invalid phone number format". The country starts on United States: set it first, since it changes the address fields. The postal code takes at most 10 characters. An address the site doubts shows "This appears to be an invalid email, please verify before proceeding", which does not stop the step. These forms often require a full home address, work history and references: what the files do not say goes back to the person.

Dropdowns are `div[role="combobox"]` showing "--" until a choice is made, such as `#info\.haveYouAppliedWithUsBefore`. "How did you hear about us" is a radio list (`info.howDidYouHearAboutUs`): choose what the answers say.

`#info\.dateAvailableToStart` takes MM/DD/YYYY and has a picker button beside it; work history dates take MM/YYYY.

`#info\.skills` is a tag field ("Type a skill and press enter"). Leave it empty unless the form requires a skill. To add one, type one value from the CV or the answers, then press Enter, which makes it a tag; repeat for each. Enter is allowed only in this field, anywhere else it is refused. A required skill the files do not say goes back to the person.

"Add Work History" and "Add Education" add rows, with the same fields numbered `.0`, `.1`. "Select Cover Letter" is a file: leave it empty unless required.

Demographic questions on the last steps are voluntary: choose the option that declines to answer.

After sending: read the page: if the form is still there, the application was not received.
