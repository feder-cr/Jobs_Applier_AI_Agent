---
name: menus
when: always
---
A field that opens a list is one of four things, and which one it is decides what works. Read it with browser_snapshot or browser_read_html before you act on it.

1. A native `<select>`: `browser_select_option` with the option's value or its label. A click on it is wasted.
2. A listbox: a box with `role="combobox"`, or a div that shows "--" or "Select One" until a choice is made. Click the box, read its options with browser_evaluate as the children of the list the page gives it, whose id is often the field's id with `__listbox` after it, then click the option by its own id, such as `#<field>__listbox__option__3`.
3. A typeahead: a text input that filters rows as you type. Type the answer's words, read the rows in its container, whose id is often the field's id with `-dropdown-list-container` after it, and click the first row. On some sites that row reads "Create <the words you typed>" and still sets the value.
4. A radio or checkbox group drawn as a menu: the input is hidden and a div beside it takes the click. Click that div by what it holds, such as `div[role="radio"]:has(input[name="Q_1"][value="true"])`.

Check the result, do not assume it. Read the field's own text back with browser_evaluate and see your answer in it. A click the tool called a success often leaves the field empty.

When a click does not land, and the tool says the event would have landed elsewhere or the element is not actionable, do not click it again: the option is under something else. Press ArrowDown as many times as the option's place in the list, then Enter, which is how a person with a keyboard does it. If the list is inside a shadow root, read the option's box with browser_evaluate, which only reads, and click its centre with browser_click_at: those numbers are viewport pixels, the same as a snapshot's `at`. Never take a point off a screenshot by eye.

When one list has beaten you three times, leave it. If the field is optional, go on without it. If it is required, call finish_form with status blocked and name that field. A form left half filled tells the person something; fifty steps spent on one menu tell them nothing. On a real form two lists took 100 of the 150 steps a form is allowed, and the rest of the form was nearly not filled at all.
