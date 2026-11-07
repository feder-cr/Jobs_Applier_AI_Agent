---
name: personio
hosts: [jobs.personio.de, jobs.personio.com]
apply_path: /apply
---
The form needs no sign-in.

Fields by name: `first_name`, `last_name`, `email`, `phone`, `birthday`, `available_from`, `salary_expectations` (ids `field-<name>`). The company's own questions are `#field-custom_attribute_<number>`. `gender` is a native select: choose by label.
The language switcher in the header is a custom combobox (`role="combobox"`): not part of the form, leave it alone.
The CV goes to `[aria-labelledby="doc-label-cv"] button`, the control under "Datei hinzufügen". The input behind it, `#doc-input-cv`, is hidden and refuses the file.
`birthday`, `available_from` and `salary_expectations` take the format their own placeholder shows.

The submit control is `form button[type="submit"]`, labelled "Bewerbung senden" or the same in the page's language, which is English on some `jobs.personio.com` postings. It stays disabled until the required fields are filled.

A Cloudflare Turnstile box can sit above the send control, and it passed on two postings of four. If the send control stays disabled with every required field filled, call finish_form with status blocked and say whether a Turnstile box was there and whether it was ticked.

After sending: read the page: if the form is still there, the application was not received.
