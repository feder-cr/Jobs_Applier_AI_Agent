---
name: bamboohr
hosts: [bamboohr.com]
---
The careers page shows the job. Click the "Apply for This Job" button; the form opens on the same page (the URL does not change). There is no sign-in.

Stable ids: `#firstName`, `#lastName`, `#email`, `#phone`, `#desiredPay`, `#websiteUrl`, `#linkedinUrl`. Labels are the reliable way to find the rest, because `FabricTextField-<n>` and `fab-select<n>` ids change between postings.

Country, state or province, and education are MUI controls: a button with `aria-label="Country <name>"` (or "State –Select–", "Province –Select–") and `aria-haspopup`, next to a native `select` with numeric values. Use the native select by its label and read back the value.

Date available is a text field with placeholder mm/dd/yyyy, beside a "Pick a date." button; type the date in the format shown.

Yes/no and multiple-choice questions are `role="radiogroup"` groups with `input[name^="customQuestionAnswers."]` radios; find each group by its `legend`. Custom short-text questions use the same name prefix.

Leave `#nickname_hpcsaf` empty: it is a hidden trap labelled "Please leave this field blank".

Resume: "Choose File" buttons: read the page to find the file input behind them.

Fill every field before you look at the captcha. Then look just above the send button for a reCAPTCHA box, tick it once, and read the page: a green check means it passed, and a second tick would undo it. An image puzzle ends the form: call finish_form with status blocked and say the captcha became a puzzle. If no box is there, do not wait for one. Both endings are normal on this site, which is why the order matters: on five postings the click did not pass and a puzzle followed, and on a later run the send button was reached with no captcha shown at all.

The send control is the `button[type=submit]` labelled "Submit Application".

After sending: read the page: if the form is still there, the application was not received.
