# Sites: what to expect

What AIHawk can do on each site, from the real forms it has been run on. Postings are counted by host in the job index (`index-202611041136230000.parquet`, 2026-11-04) and rounded to the nearest thousand.

| Site | Postings in the index | Status | What stops it |
|---|---|---|---|
| Greenhouse (boards and job-boards hosts, EU too) | ~200k | sent and confirmed on a real form | One real send, Bottomline (one send only). Robinhood and Adyen stopped at required questions the CV and answers do not cover; one Robinhood run failed. |
| Ashby (jobs.ashbyhq.com) | ~76k | sent and confirmed on a real form | One real send, Mollie (one send only). Snowflake and Glia stopped at required questions; Lemonade's posting was closed; on ElevenLabs and XYZ Reality the Submit button stayed disabled. |
| Lever (jobs.lever.co) | ~101k | filled on real forms, send not confirmed | One click on CesiumAstro, not confirmed. Two Dun & Bradstreet forms stopped at required questions the CV and answers do not cover. |
| JazzHR (applytojob.com) | ~154k | stops at a captcha most of the time | reCAPTCHA did not pass with a click on 5 postings; it often turned into an image puzzle. |
| BambooHR (bamboohr.com) | ~87k | stops at a captcha most of the time | reCAPTCHA did not pass with a click on 5 postings; it often turned into an image puzzle. |
| Personio (jobs.personio.de, jobs.personio.com) | ~64k | stops at a captcha about half the time | Turnstile passed on 2 of 4 postings. A real Allgeier Inovar form ended blocked. |
| SmartRecruiters (jobs.smartrecruiters.com) | ~519k | does not finish yet | Five real postings (ServiceNow, Delivery Hero, Wise, LinkedIn, Wabtec), none reached a send control. The CV attaches (the dropzone `[data-test='resume-upload'] >> div`, confirmed by screenshot) and the UK phone was accepted, with the country chosen in the picker. LinkedIn and Wabtec stopped at the required Experience (and Education) sections, which the test CV cannot fill; Delivery Hero passed step 1 and stopped at Preliminary questions it has no answers for; ServiceNow and Wise ran out of the 7-minute limit. The test phone was changed to +44 7911 123456: the owner's +44 7700 900123 is refused by the widget. |
| Recruitee (recruitee.com) | ~26k | filled on real forms, no send tried yet | Two real forms (Athora), both stopped at required questions. Nothing sent. |
| Teamtailor (teamtailor.com) | ~30k | filled on real forms, no send tried yet | Two real forms (Huawei), both stopped at required questions. Nothing sent. |
| Workable (apply.workable.com) | ~35k | filled on real forms, no send tried yet | Filled in a test that stopped before sending, then asked what the answers do not cover. The one send attempt found a closed posting. Thin evidence. |
| Paylocity (recruiting.paylocity.com) | ~228k | filled on real forms, no send tried yet | Filled in a test that stopped before sending, then asked what the answers do not cover. The one send attempt found a closed posting. Thin evidence. |
| Breezy (breezy.hr) | ~50k | not tried on a real form | No real run yet. |
| Workday (myworkdayjobs.com, myworkdaysite.com) | ~1,481k | not supported | Needs an account. |
| iCIMS (icims.com) | ~622k | not supported | Needs an account. |

Postings per status, in the index: sent and confirmed ~277k; filled, send not confirmed ~101k; filled, no send tried yet ~319k; stops at a captcha most or half of the time ~305k; does not finish yet ~519k; not supported ~2,103k; not tried on a real form ~50k.

Notes:

- "Sent and confirmed" rests on one send per site. Treat it as "it can send", not "it sends reliably".
- A form asks the person for whatever the CV and answers do not cover, then tries again. In trials with test answers most forms stopped there, as they should.
- Greenhouse's confirmation page was not saved, so its plugin keeps generic wording. Ashby's is quoted in its plugin.
- A captcha shown as a box is clicked, twice at most, as a person would. It is not solved: on a puzzle the form stops as blocked, for the person to do by hand.
