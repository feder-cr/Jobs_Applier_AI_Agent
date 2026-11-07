---
name: personio
hosts: [jobs.personio.de, jobs.personio.com]
apply_path: /apply
---
The form needs no sign-in.

Fields by name: `first_name`, `last_name`, `email`, `phone`, `birthday`, `available_from`, `salary_expectations` (ids `field-<name>`). `gender` is a native select: choose by label.
The language switcher in the header is a custom combobox (`role="combobox"`): not part of the form, leave it alone.
Upload the CV with the "Datei hinzufügen" control: read the page to find the file input behind it.
`birthday`, `available_from` and `salary_expectations` take the format their own placeholder shows.

The submit control is "Bewerbung senden", or the same control in the page's language on a `jobs.personio.com` posting. It stays disabled until the required fields are filled.

A Cloudflare Turnstile box can sit above the send control, and it passed on two postings of four. If the send control stays disabled with every required field filled, call finish_form with status blocked and say whether a Turnstile box was there and whether it was ticked.

After sending: read the page: if the form is still there, the application was not received.
