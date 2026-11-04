---
name: greenhouse
hosts: [greenhouse.io]
---
The page shows the job and, below it, the form. There is no need to click "Apply": the fields are already on the page. Do not use "Autofill my application", Dropbox or Google Drive.

`#first_name`, `#last_name`, `#email` and `#phone` are the basics. Upload the CV to `#resume`, a file input hidden by CSS. `#cover_letter` is a file too: leave it empty unless it is required.

Questions a company adds have ids such as `#question_39108683002`: find each by its label. A required label ends with a `*`. Many fields look like text but are lists (`role="combobox"`, showing "Select..."), such as `#country` and `#candidate-location`. Each has a "Toggle flyout" button beside it. Type the value, then click the suggestion that appears, and read the page to check it was taken: text left unselected is not an answer.

Gender, ethnicity, veteran and disability questions are lists too (`#gender`, `#hispanic_ethnicity`, `#veteran_status`).

Education can repeat: "Add another" adds a row (`#school--0`, `#degree--0`, `#discipline--0`). Its months are lists (`#start-month--0`) and its years are number fields (`#start-year--0`).

A field's error goes in an element with the id `<field>-error`, which the field names in aria-describedby.

Pages at boards.greenhouse.io have the same fields and layout, with more questions.

The submit control is the button "Submit application".

After sending: the page usually changes to the employer's confirmation page, which says the organization received the application. The wording is set per job board, so do not look for one exact phrase. The confirmation page is usually at the job's `/confirmation` address (seen in public search results). If the form is still on the page, the application was not received.
