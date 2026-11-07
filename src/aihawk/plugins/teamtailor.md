---
name: teamtailor
hosts: [teamtailor.com]
apply_path: /applications/new
---
The application form is one page, and its labels follow the company's language, so go by each field's name. Questions come first, then "Personal details", the attachments, the consent box and "Submit application". Read the whole page before you fill it.

Questions are inputs named `candidate[answers_attributes][N][...]`. A yes/no is a radio whose visible label is "Yes"/"No" (or "Ja"/"Nej"); its input is hidden, so click the label. A list of boxes is a checkbox group. A number is a slider with an "Edit number" button. Some questions are custom menus: a button with `aria-haspopup="menu"` shows the current value as its text, and a hidden required input behind it holds the value the form sends. Where the options are buttons with `role="menuitem"` and a `data-v` attribute, click the one that matches the answer by its selector, for example `button[role="menuitem"][data-v="Engineering"]`.

Phone: `input[name="candidate[phone]"]`, with the country set by the button "Change country" beside it. The number is checked only when the form is sent, and an error such as "Phone is not valid" (in the page's language) then shows beside the field, with the form still there: type the number again from the answers with its + code, and if it is refused a second time, call finish_form with status blocked and name the phone. Numbers in the range +44 7700 900000 to 900999 are refused this way. A number typed whole with its + code sets the country by itself. Location: `candidate[location][query]` is a combobox with "Start typing your address". Type and pick from the suggestions, then check it was taken.

A tag field (Skills, for example) turns each typed skill into a chip when Enter is pressed in its input. The chips are what the form sends; text left in the input is not sent.

Upload the CV to `#candidate_resume_remote_url` ("Upload resume"). Do not click "Apply with LinkedIn". The cookie banner is open on the page: decline or leave it, do not treat it as a question. Sign-in is not needed. The send control is `input[name=commit]`, "Submit application": report ready only on the page before it.

After sending: the address gains `/thanks/` and the page says it received the application, in Spanish "Gracias por la solicitud" and "Hemos recibido tu solicitud". Read the page: if the form is still there, the application was not received.
