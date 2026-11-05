# Sites: what to expect

What AIHawk can do on each site, from the real forms it has been run on. Postings are counted by host in the job index (`index.parquet` in the release built 2026-11-04) and rounded to the nearest thousand.

| Site | Postings in the index | Status | What stops it |
|---|---|---|---|
| Greenhouse (boards and job-boards hosts, EU too) | ~200k | sent and confirmed on a real form | One real send (one send only). Others stopped at required questions the CV and answers do not cover. |
| Ashby (jobs.ashbyhq.com) | ~76k | sent and confirmed on a real form | One real send (one send only). Others stopped at required questions, or found a closed posting, or a Submit button that stayed disabled. |
| Lever (jobs.lever.co, jobs.eu.lever.co) | ~101k | sent and confirmed on a real form | One real send through the app as it is now: the page said "Application submitted!" at an address ending in `/thanks`. |
| JazzHR (applytojob.com) | ~154k | stops at a captcha most of the time | reCAPTCHA did not pass with a click on 5 postings; it often turned into an image puzzle. |
| BambooHR (bamboohr.com) | ~87k | stops at a captcha most of the time | reCAPTCHA did not pass with a click on 5 postings; it often turned into an image puzzle. |
| Personio (jobs.personio.de, jobs.personio.com) | ~64k | stops at a captcha about half the time | Turnstile passed on 2 of 4 postings. A real form ended blocked. |
| SmartRecruiters (jobs.smartrecruiters.com) | ~519k | reaches the send button, no send tried yet | One real form filled through every step in about 11 minutes, and the send button named. Not clicked. |
| Recruitee (recruitee.com) | ~26k | sent and confirmed on a real form | One real send (one send only). Other forms stopped at required questions that test answers do not fit. |
| Teamtailor (teamtailor.com) | ~30k | sent and confirmed on a real form | One real send (one send only). An earlier click was refused for its test phone number, so nothing went then. Other forms stopped at required questions. |
| Workable (apply.workable.com) | ~35k | reaches the send button, no send tried yet | One real form filled in about 7 minutes, and the send button named. Not clicked. |
| Paylocity (recruiting.paylocity.com) | ~228k | reaches the send button, no send tried yet | One real form of 5 steps filled in about 27 minutes, and the send button named. Not clicked. |
| Breezy (breezy.hr) | ~50k | sent and confirmed on a real form | One real send (one send only). |
| Workday (myworkdayjobs.com, myworkdaysite.com) | ~1,481k | not supported | Needs an account. |
| iCIMS (icims.com) | ~622k | not supported | Needs an account. |

Postings per status, in the index: sent and confirmed ~484k; reaches the send button, no send tried yet ~782k; stops at a captcha most or half of the time ~305k; not supported ~2,103k.

Notes:

- A form no longer stops at what the CV does not say. Lever's send was made after that change; the others behind "sent and confirmed" were made before it, when other forms on those sites stopped at required questions.
- "Sent and confirmed" rests on one send per site. Treat it as "it can send", not "it sends reliably".
- A form asks the person for whatever the CV and answers do not cover, then tries again. In trials with test answers most forms stopped there, as they should.
- Greenhouse's confirmation page was not saved, so its plugin keeps generic wording. Ashby's, Breezy's, Recruitee's and Teamtailor's are quoted in their plugins.
- A captcha shown as a box is clicked, twice at most, as a person would. It is not solved: on a puzzle the form stops as blocked, for the person to do by hand.
