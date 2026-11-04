---
name: teamtailor
hosts: [teamtailor.com]
apply_path: /applications/new
---
The application form is one page. Questions come first, then "Personal details", the attachments, the consent box and "Submit application". Read the whole page before you fill it.

Questions are inputs named `candidate[answers_attributes][N][...]`. A yes/no is a radio whose visible label is "Yes"/"No" (or "Ja"/"Nej"); its input is hidden, so click the label. A list of boxes is a checkbox group. A number is a slider with an "Edit number" button. Some questions are custom menus: a button with aria-haspopup="menu" and a hidden required input behind it. Read the options from the page before choosing.

Phone: `input[name="candidate[phone]"]`, with the country set by the button "Change country" beside it. Location: `candidate[location][query]` is a combobox with "Start typing your address". Type and pick from the suggestions, then check it was taken.

Upload the CV to `#candidate_resume_remote_url` ("Upload resume"). Do not click "Apply with LinkedIn". The cookie banner is open on the page: decline or leave it, do not treat it as a question. Sign-in is not needed. The send control is `input[name=commit]`, "Submit application": report ready only on the page before it.

After sending: Teamtailor usually sends a "thank you for your application" email, for applications through the job's application email and through the career site. Read the page: if the form is still there, the application was not received.
