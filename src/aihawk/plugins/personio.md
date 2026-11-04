---
name: personio
hosts: [jobs.personio.de, jobs.personio.com]
---
The job page has no form. Click the apply link whose href is `/job/<id>/apply` (text "Auf diese Stelle bewerben" or "Apply for this job"). The form is on that page, with no sign-in.

Fields by name: `first_name`, `last_name`, `email`, `phone`, `birthday`, `available_from`, `salary_expectations` (ids `field-<name>`). `gender` is a native select: choose by label.
The language switcher in the header is a custom combobox (`role="combobox"`): not part of the form, leave it alone.
Upload the CV with the "Datei hinzufügen" control: read the page to find the file input behind it.
The submit control is "Bewerbung senden". It stays disabled until the required fields are filled.
