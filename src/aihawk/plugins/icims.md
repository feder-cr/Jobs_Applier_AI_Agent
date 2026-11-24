---
name: icims
hosts: [icims.com]
---
These pages never reach network idle: wait for the DOM, or 45 seconds go each time.

The form has steps, the Candidate Profile first, then pages of questions: "Candidate Questions" at `/questions`, "EEO", "Job Specific Questions". The first page asks for `#email`, with two boxes the form requires, `#accept_privacy` and `#accept_gdpr`. An email the site does not know goes to the Candidate Profile; one it knows signs in with the password, through `login.icims.com`, which is iCIMS's own sign-in, and may then offer a passkey: decline it. A step already done shows as Completed: leave it as it is.

A button labelled Submit sends the application, whatever the step list shows after it: there is no review page. On a real form it was `#quesp_form_submit_i` on "Job Specific Questions", step 4 of 5, and the application went then, sent by the model and not by the app. "Submit Profile", `#cp_form_submit_i` on the Candidate Profile, is the one that depends on the step list: with more steps after the profile it makes the account and moves on, and on a posting whose list says "Step 1 of 1" it sends the application (on a real form it went then, sent by the model). The button that sends is the send control: name it in finish_form and never click it.

The Candidate Profile makes the account: `PersonProfileFields.Login` is the email again, `PersonProfileFields.Password` and `PersonProfileFields.Password_Confirm` take the `account_password` from the answers. When more steps follow, sending this page with `#cp_form_submit_i` creates the account: from then on, finish with made_account true. When it is the only step, the account is made with the send, so finish with made_account true then too.

The CV goes first, before anything is typed. Its control is `#PortalProfileFields.Resume_File`, behind "My Computer"; giving it the file sends the page, which keeps the file, renamed like `Resume202611070210.pdf`, and comes back without what had been typed. Seen on a real form: three uploads after the fields were filled, each one wiping them, 10 minutes and about 70 steps lost. After the upload, read the page again, check what iCIMS read from the CV, then fill the rest. A profile that already holds a CV keeps it: "Replace Resume" did not replace it on a real form, so leave it.

The lists are iCIMS's own: the native select is hidden, and `#<select id>_icimsDropdown` opens it. Type into `input[aria-controls="<select id>_dropdown-results"]`, click the `li` whose id starts `result-selectable_<select id>_`, then read `#<select id>_fakeSelected_icimsDropdown` to see what was chosen. For Italy the state list says "No states available": leave it.
