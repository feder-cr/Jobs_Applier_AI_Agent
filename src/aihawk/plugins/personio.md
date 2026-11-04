---
name: personio
hosts: [jobs.personio.de, jobs.personio.com]
apply_path: /apply
---
The form needs no sign-in.

Fields by name: `first_name`, `last_name`, `email`, `phone`, `birthday`, `available_from`, `salary_expectations` (ids `field-<name>`). `gender` is a native select: choose by label.
The language switcher in the header is a custom combobox (`role="combobox"`): not part of the form, leave it alone.
Upload the CV with the "Datei hinzufügen" control: read the page to find the file input behind it.
The submit control is "Bewerbung senden". It stays disabled until the required fields are filled.

After sending: nothing is established for Personio. No saved page, real send or documentation shows what follows a submit. Read the page: if the form is still there, the application was not received.
