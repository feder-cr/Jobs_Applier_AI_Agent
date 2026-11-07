---
name: icims
hosts: [icims.com]
---
Every iCIMS page wraps the form in an iframe, `#icims_content_iframe`, and the site strips `in_iframe=1` from an address on the way. Open the form itself at the posting's address with its query replaced by `?mode=apply&apply=yes&in_iframe=%31`: `%31` is the same 1, written so the site's script does not strip it, and the form is then the page and not a frame. Keep `in_iframe=%31` on every address you open after that. Seen on a real form: about 20 steps lost before this was found. These pages never reach network idle: wait for the DOM, or 45 seconds go each time.

The form has steps, the Candidate Profile first, then pages of questions: "Candidate Questions" at `/questions`, "EEO", "Job Specific Questions". The first page asks for `#email`, with two boxes the form requires, `#accept_privacy` and `#accept_gdpr`. An email the site does not know goes to the Candidate Profile; one it knows signs in with the password, through `login.icims.com`, which is iCIMS's own sign-in, and may then offer a passkey: decline it. A step already done shows as Completed: leave it as it is.

A button labelled Submit sends the application, whatever the step list shows after it: there is no review page. On a real form it was `#quesp_form_submit_i` on "Job Specific Questions", step 4 of 5, and the application went then, sent by the model and not by the app. That button is the send control: name it in finish_form and never click it.

The Candidate Profile makes the account: `PersonProfileFields.Login` is the email again, `PersonProfileFields.Password` and `PersonProfileFields.Password_Confirm` take the `account_password` from the answers. Sending this page with `#cp_form_submit_i` creates the account, whatever comes after: from then on, finish with made_account true.

The CV goes first, before anything is typed. Its control is `#PortalProfileFields.Resume_File`, behind "My Computer"; giving it the file sends the page, which keeps the file, renamed like `Resume202611070210.pdf`, and comes back without what had been typed. Seen on a real form: three uploads after the fields were filled, each one wiping them, 10 minutes and about 70 steps lost. After the upload, read the page where it is, inside `#icims_content_iframe` if the frame is back, check what iCIMS read from the CV, then fill the rest. A profile that already holds a CV keeps it: "Replace Resume" did not replace it on a real form, so leave it.

The lists are iCIMS's own: the native select is hidden, and `#<select id>_icimsDropdown` opens it. Type into `input[aria-controls="<select id>_dropdown-results"]`, click the `li` whose id starts `result-selectable_<select id>_`, then read `#<select id>_fakeSelected_icimsDropdown` to see what was chosen. For Italy the state list says "No states available": leave it.
