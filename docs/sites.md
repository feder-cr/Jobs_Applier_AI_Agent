# Sites: what to expect

What AIHawk can do on each site, from the real forms it has been run on. Postings are counted by host in the job index (`index.parquet` in the release built 2026-11-04) and rounded to the nearest thousand.

| Site | Postings in the index | Status | What stops it |
|---|---|---|---|
| Greenhouse (boards and job-boards hosts, EU too) | ~200k | sent and confirmed on a real form | One real send, Bottomline (one send only). Robinhood and Adyen stopped at required questions the CV and answers do not cover; one Robinhood run failed. |
| Ashby (jobs.ashbyhq.com) | ~76k | sent and confirmed on a real form | One real send, Mollie (one send only). Snowflake and Glia stopped at required questions; Lemonade's posting was closed; on ElevenLabs and XYZ Reality the Submit button stayed disabled. |
| Lever (jobs.lever.co, jobs.eu.lever.co) | ~101k | filled on real forms, send not confirmed | Two clicks, neither confirmed by the page's words. On Audinate the address moved to `/thanks`, whose page says "Application submitted!" to any visitor, but the text had been read before the page changed, a timing fault since fixed. On CesiumAstro nothing confirmed it. Other forms stopped at required questions, or at the right to work or a background check, which a test cannot answer. |
| JazzHR (applytojob.com) | ~154k | stops at a captcha most of the time | reCAPTCHA did not pass with a click on 5 postings; it often turned into an image puzzle. |
| BambooHR (bamboohr.com) | ~87k | stops at a captcha most of the time | reCAPTCHA did not pass with a click on 5 postings; it often turned into an image puzzle. |
| Personio (jobs.personio.de, jobs.personio.com) | ~64k | stops at a captcha about half the time | Turnstile passed on 2 of 4 postings. A real Allgeier Inovar form ended blocked. |
| SmartRecruiters (jobs.smartrecruiters.com) | ~519k | filled on real forms, no send tried yet | The CV attaches and the phone is taken. Seven real postings stopped before a send control: some at required questions, some at the right to work or a statement that the answers are true, which a test cannot answer. Two ran out of steps there. |
| Recruitee (recruitee.com) | ~26k | sent and confirmed on a real form | One real send, Accso (one send only). Other forms stopped at required questions that test answers do not fit. |
| Teamtailor (teamtailor.com) | ~30k | sent and confirmed on a real form | One real send, knowmad mood (one send only). The first click there was refused for its test phone number, so nothing went then. Other forms stopped at required questions. |
| Workable (apply.workable.com) | ~35k | filled on real forms, no send tried yet | Two Activate Interactive forms stopped at a required work-authorisation question, which the test does not answer. The earlier send attempt found a closed posting. Nothing sent. |
| Paylocity (recruiting.paylocity.com) | ~228k | filled on real forms, no send tried yet | Two forms (Hartz, Advanced Air Systems) asked many required questions, one of them about the right to work, which the test does not answer. The earlier send attempt found a closed posting. Nothing sent. |
| Breezy (breezy.hr) | ~50k | sent and confirmed on a real form | One real send, Eureka Robotics (one send only). |
| Workday (myworkdayjobs.com, myworkdaysite.com) | ~1,481k | not supported | Needs an account. |
| iCIMS (icims.com) | ~622k | not supported | Needs an account. |

Postings per status, in the index: sent and confirmed ~383k; filled, send not confirmed ~101k; filled, no send tried yet ~782k; stops at a captcha most or half of the time ~305k; not supported ~2,103k.

Notes:

- "Sent and confirmed" rests on one send per site. Treat it as "it can send", not "it sends reliably".
- A form asks the person for whatever the CV and answers do not cover, then tries again. In trials with test answers most forms stopped there, as they should.
- Greenhouse's confirmation page was not saved, so its plugin keeps generic wording. Ashby's, Breezy's, Recruitee's and Teamtailor's are quoted in their plugins.
- A captcha shown as a box is clicked, twice at most, as a person would. It is not solved: on a puzzle the form stops as blocked, for the person to do by hand.
