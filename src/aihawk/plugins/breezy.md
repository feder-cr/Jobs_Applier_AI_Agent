---
name: breezy
hosts: [breezy.hr]
apply_path: /apply
---
The page follows the browser's language, so labels may not be in English: go by the field's name.

Fields are named `cName`, `cEmail` and `cPhoneNumber`, which is one text field with no country list. `cAddress` and a desired salary, with its currency and period beside it, may be required. Some forms also have `cSummary` for a summary of experience and `cCoverLetter`. A required summary may be written only from the CV's own words. Leave `smsConsent` unticked unless the answers file says otherwise. Do not use the Indeed or LinkedIn links.

The CV upload is a link, "Carica Curriculum" on an Italian page and "Attached" once a file is in place, near the top, with no file input and no id: select it by that text. It carries a "Required" mark when the CV is required. Uploading may fill fields from the CV, so read the page again afterwards and correct them.

"Aggiungi posizione" (add position) and "Aggiungi Studi e formazione" (add education) are links that add an entry: leave them empty unless required.

Questions a company adds have names like `section_..._question_0`, as radio buttons, native select lists or text: find each by the text before it. Some forms ask many required questions about licences or experience: what the files do not answer goes back to the person.

Race, gender and veteran questions (`race_ethnicity`, `gender`, `eeoc.veteran_status`) have an "unspecified" choice: pick that. Their ids do not match their labels (`#race_hispanic` is labelled "asian"), so choose by the label, not the id.

The send control is `button[type='submit']`, the form's last control, labelled "Invia candidatura" on an Italian page.

After sending: the page says "Application Submitted" and "Your application has been submitted successfully. Good luck!", at an address ending in `/apply/submitted`. A submitted application can show as applied while the candidate's email is still unverified, because Breezy counts it as applied at submit. Breezy may send a four-digit verification code by email. Read the page: if the form is still there, the application was not received.
