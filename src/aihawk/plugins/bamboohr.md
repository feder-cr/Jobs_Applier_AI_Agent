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

Resume: "Choose File" buttons: read the page to find the file input behind them. The send control is the `button[type=submit]` labelled "Submit Application".

After sending: read the page: if the form is still there, the application was not received.
