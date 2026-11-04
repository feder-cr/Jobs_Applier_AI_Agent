---
name: workable
hosts: [apply.workable.com]
apply_path: /apply
---
The form loads a moment after the page: if it looks empty, read the page again.

`#firstname`, `#lastname`, `#email` and `#input_phone` are the basics, and the phone has a country button before it. Upload the CV to the file input under "Resume", which has an id like `#input_files_input_...`, so select it with `input[type="file"]` in that section. Do not use "Autofill application" or "Import resume from".

"Experience" and "Education" with a "+ Add" button are usually optional: leave them empty unless required.

Questions a company adds have names like `CA_30078`: find each by its label. A field showing "Select an option…" is a list: type, then click the option. Radio answers have generated ids, so select them by `input[name="CA_41201"]` with the right position, and check the result by reading the page.

A `gdpr` checkbox states that the person read the privacy notice. Tick it only if the answers file says they accept such notices; otherwise ask.

The submit control is the button "Submit application".
