# Sites: what to expect

What AIHawk can do on each site, from the real forms it has been run on. Postings are counted by host in the job index (`index-202611041136230000.parquet`, 2026-11-04) and rounded to the nearest thousand.

| Site | Postings in the index | Status | What stops it |
|---|---|---|---|
| Greenhouse (boards and job-boards hosts, EU too) | ~200k | sent and confirmed on a real form | One real send, Bottomline (one send only). Robinhood and Adyen stopped at required questions the CV and answers do not cover; one Robinhood run failed. |
| Ashby (jobs.ashbyhq.com) | ~76k | sent and confirmed on a real form | One real send, Mollie (one send only). Snowflake and Glia stopped at required questions; Lemonade's posting was closed; on ElevenLabs and XYZ Reality the Submit button stayed disabled. |
| Lever (jobs.lever.co) | ~101k | filled on real forms, send not confirmed | One click on CesiumAstro, not confirmed. Two Dun & Bradstreet forms stopped at required questions the CV and answers do not cover. |
| JazzHR (applytojob.com) | ~154k | stops at a captcha most of the time | reCAPTCHA did not pass with a click on 5 postings; it often turned into an image puzzle. |
| BambooHR (bamboohr.com) | ~87k | stops at a captcha most of the time | reCAPTCHA did not pass with a click on 5 postings; it often turned into an image puzzle. |
| Personio (jobs.personio.de, jobs.personio.com) | ~64k | does not finish yet | Turnstile passed on 2 of 4 postings. A real Allgeier Inovar form ended blocked. |
| SmartRecruiters (jobs.smartrecruiters.com) | ~519k | does not finish yet | Three real postings (ServiceNow, Delivery Hero, Wise), none reached the end: the CV did not attach and the phone number was refused. |
| Recruitee (recruitee.com) | ~26k | does not finish yet | Two real forms (Athora), both stopped at required questions. Nothing sent. |
| Teamtailor (teamtailor.com) | ~30k | does not finish yet | Two real forms (Huawei), both stopped at required questions. Nothing sent. |
| Workable (apply.workable.com) | ~35k | does not finish yet | One real attempt, on a closed posting, so no form was reached. Thin evidence. |
| Paylocity (recruiting.paylocity.com) | ~228k | does not finish yet | One real attempt, on a closed posting, so no form was reached. Thin evidence. |
| Breezy (breezy.hr) | ~50k | not tried on a real form | No real run yet. |
| Workday (myworkdayjobs.com, myworkdaysite.com) | ~1,481k | not supported | Needs an account. |
| iCIMS (icims.com) | ~622k | not supported | Needs an account. |

Postings per status, in the index: sent and confirmed ~277k; filled, send not confirmed ~101k; stops at a captcha most of the time ~241k; does not finish yet ~902k; not supported ~2,102k; not tried on a real form ~50k.

Notes:

- "Sent and confirmed" rests on one send per site. Treat it as "it can send", not "it sends reliably".
- In the real-forms trial (11 sites, 6 models, 66 runs) no run reached the submit step.
- Greenhouse's confirmation page was not saved, so its plugin keeps generic wording. Ashby's is quoted in its plugin.
- Captchas are never passed by AIHawk. A run that meets one stops and says so.
