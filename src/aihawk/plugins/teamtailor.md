---
name: teamtailor
hosts: [teamtailor.com]
apply_path: /applications/new
---
The application form is one page. Questions come first, then "Personal details", the attachments, the consent box and "Submit application". Read the whole page before you fill it.

Questions are inputs named `candidate[answers_attributes][N][...]`. A yes/no is a radio whose visible label is "Yes"/"No" (or "Ja"/"Nej"); its input is hidden, so click the label. A list of boxes is a checkbox group. A number is a slider with an "Edit number" button. Some questions are custom menus: a button with aria-haspopup="menu" shows the current value as its text, and a hidden required input behind it holds the value the form sends. To answer one, click the button to open it, then read the options from the page (browser_read_text, or a snapshot taken after it opens). Where the options are buttons with role="menuitem" and a data-v attribute, click the one that matches the answer by its selector, for example `button[role="menuitem"][data-v="Engineering"]`. Check that it took: the button's text shows the chosen value and the hidden input reads the same value (browser_evaluate). A click by coordinates is the last resort.

Phone: `input[name="candidate[phone]"]`, with the country set by the button "Change country" beside it. Location: `candidate[location][query]` is a combobox with "Start typing your address". Type and pick from the suggestions, then check it was taken.

A tag field (Skills, for example) turns each typed skill into a chip when Enter is pressed in its input. The chips are what the form sends; text left in the input is not sent. The browser tool refuses Enter unless the field's own placeholder or aria-label asks for it. Do not send the key by script to get around that, and do not submit with a required tag missing. If a required tag cannot be added, call finish_form with status blocked, name each missing tag, and say why.

Upload the CV to `#candidate_resume_remote_url` ("Upload resume"). Do not click "Apply with LinkedIn". The cookie banner is open on the page: decline or leave it, do not treat it as a question. Sign-in is not needed. The send control is `input[name=commit]`, "Submit application": report ready only on the page before it.

After sending: Teamtailor usually sends a "thank you for your application" email, for applications through the job's application email and through the career site. Read the page: if the form is still there, the application was not received.
