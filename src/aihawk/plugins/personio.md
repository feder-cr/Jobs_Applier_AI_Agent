---
name: personio
hosts: [jobs.personio.de, jobs.personio.com]
apply_path: /apply
---
The form needs no sign-in.

Fields by name: `first_name`, `last_name`, `email`, `phone`, `birthday`, `available_from`, `salary_expectations` (ids `field-<name>`). `gender` is a native select: choose by label.
The language switcher in the header is a custom combobox (`role="combobox"`): not part of the form, leave it alone.
Upload the CV with the "Datei hinzufügen" control: read the page to find the file input behind it.
`birthday`, `available_from` and `salary_expectations` take the format their own placeholder shows: read it before typing, and fill only what the page marks required.

The submit control is "Bewerbung senden", or the same control in the page's language on a `jobs.personio.com` posting. It stays disabled until the required fields are filled.

A Cloudflare Turnstile box can sit above the send control. Fill every field first, then tick it once if it is a box to tick, and read the page. It passed on two postings of four, and on the others the form ended blocked, so do not spend steps on it: if the send control is still disabled with every required field filled, call finish_form with status blocked and say whether a Turnstile box was there and whether it was ticked.

After sending: read the page: if the form is still there, the application was not received.
