---
name: workday
hosts: [myworkdayjobs.com, myworkdaysite.com]
apply_path: /apply
---
A Workday form needs a candidate account on the company's own site: every company is a separate account, and the browser profile keeps each sign-in. The pages are one web app that redraws itself without changing address much, so after every click read the page again with browser_snapshot. Controls are best found by their `data-automation-id`, as `[data-automation-id="email"]`; the `id` attributes are numbered and change.

The page at `/apply` is "Start Your Application" with `applyManually` ("Apply Manually"), `autofillWithResume`, `useMyLastApplication` and a LinkedIn frame: click `applyManually`. Do not use "Autofill with Resume", "Sign in with Google", "Sign in with LinkedIn" or the LinkedIn frame: the account is made with the candidate's email, never through another service.

Sign in or create the account. The step "Create Account/Sign In" shows `SignInWithEmailButton` ("Sign in with email"): click it. The sign-in form has `email`, `password`, `signInSubmitButton` ("Sign In"), `createAccountLink` ("Create Account") and `forgotPasswordLink`. Sign in with the candidate's email and the `account_password` from the answers; if the site says the account does not exist, click `createAccountLink`. The create form has `email`, `password`, `verifyPassword`, the checkbox `createAccountCheckbox` ("I agree", the terms and the privacy notice of the company) and `createAccountSubmitButton` ("Create Account"). The password rules are printed above the form: at least 8 characters with upper case, lower case, a digit and a special character. Use the `account_password` from the answers, the same on every company, so the candidate can sign in again; if the answers have none, call finish_form with status blocked and say so.

Both forms carry a hidden text field `beecatcher` (name `website`, "This input is for robots only"): never type in it.

A code sent by email. After "Create Account", or at sign-in, the site may ask to verify the email address with a code it sent. Workday's messages are expected from an address at `myworkday.com`, often `<company>@myworkday.com`, though no real Workday mail has been read: search the sender domain `myworkday.com` first and the subject "verification code" second, as the mail instructions say, then come back to the form's address, read the page again and type the code where it asks. If no such message is there within the two looks the mail instructions allow, call finish_form with status blocked and say no code arrived.

If sign-in with the account_password fails and the site says the account already exists, call finish_form with status blocked and say the account needs a password the answers do not hold. Do not try another password: Workday locks an account after a few attempts, and that account is the person's only way back to this company.

The steps after sign-in, shown in the progress bar: "My Information", "My Experience", "Application Questions", "Voluntary Disclosures", "Self Identify" on some sites, and "Review". Each step ends with `[data-automation-id="bottom-navigation-next-button"]` ("Save and Continue"), and the page lists its errors at the top in red when a required field is missing: read them and fix each field before clicking again.

What follows was read on a local copy of this flow, not on a company's Workday, so treat it as what to expect and the page as the truth. A step named here that the progress bar does not show is simply not there: go on. A field named here that the page does not show is not there either: use the labels the page gives. A required field with no answer in the CV or the answers ends the form: call finish_form with status blocked and name that field.

My Information: "How Did You Hear About Us?" is a list and "Have you previously worked for ..." a yes or no. Then the legal name (`legalNameSection_firstName`, `legalNameSection_lastName`), the address (`addressSection_addressLine1`, `addressSection_city`, `addressSection_postalCode`, with a region list on most countries) and the phone (`phone-device-type`, a country code list, `phone-number`). Answer "previously worked for" with yes only if the CV names that company itself.

My Experience: work experience, education and skills are blocks added with "Add" buttons and are usually optional; leave them empty unless required, the CV says it. Upload the CV with the file input in the "Resume/CV" section, `[data-automation-id="file-upload-input-ref"]` or the `input[type="file"]` there, and wait until the file name shows. Websites and social links are optional.

Application Questions: the company's own questions, mostly lists and yes or no, all usually required. Voluntary Disclosures and Self Identify: demographic questions with a "prefer not to say" or "I do not wish to answer" option, and a "Terms and Conditions" checkbox that is required.

Review shows every answer; the send control is `[data-automation-id="bottom-navigation-next-button"]` there, labelled "Submit".

After sending: the page says the application was submitted and offers the candidate home or more jobs; the address leaves `/apply`. If the Review page is still there with its "Submit" button, or errors show at the top, the application was not received.
