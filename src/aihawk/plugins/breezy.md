---
name: breezy
hosts: [breezy.hr]
apply_path: /apply
---
The page follows the browser's language, so labels may not be in English: go by the field's name.

Fields are named `cName`, `cEmail`, `cPhoneNumber`, `cSummary` for a summary of experience, and `cCoverLetter`. A required summary may be written only from the CV's own words. Leave `smsConsent` unticked unless the answers file says otherwise. Do not use the Indeed or LinkedIn buttons.

The CV upload is a button near the top, with no visible file input: pass that button's selector to the upload tool. Uploading may fill fields from the CV, so read the page again afterwards and correct them.

Questions a company adds have names like `section_..._question_0`, as radio buttons or lists: find each by the text before it. Some forms ask many required questions about licences or experience: what the files do not answer goes back to the person.

The submit control is the form's last button.
