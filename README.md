<div align="center">
<picture>
  <source media="(max-width: 374px) and (prefers-color-scheme: dark)" srcset="https://raw.githubusercontent.com/feder-cr/Jobs_Applier_AI_Agent/main/assets/banner-small-dark.gif">
  <source media="(max-width: 374px)" srcset="https://raw.githubusercontent.com/feder-cr/Jobs_Applier_AI_Agent/main/assets/banner-small-light.gif">
  <source media="(max-width: 1239px) and (prefers-color-scheme: dark)" srcset="https://raw.githubusercontent.com/feder-cr/Jobs_Applier_AI_Agent/main/assets/banner-phone-dark.gif">
  <source media="(max-width: 1239px)" srcset="https://raw.githubusercontent.com/feder-cr/Jobs_Applier_AI_Agent/main/assets/banner-phone-light.gif">
  <source media="(prefers-color-scheme: dark)" srcset="https://raw.githubusercontent.com/feder-cr/Jobs_Applier_AI_Agent/main/assets/banner-dark.gif">
  <img alt="AIHawk. An animation: the app starts from a terminal and narrows 10 million job postings to the 25 that fit a resume; you pick three and say yes once; a Firefox window fills each form and submits it; the app shows them sent. An illustration, with invented companies and an invented person." src="https://raw.githubusercontent.com/feder-cr/Jobs_Applier_AI_Agent/main/assets/banner-light.gif" width="100%">
</picture>
<h3 align="center">An AI job application bot: automated job search, then it auto-applies to the jobs that match your resume, while you sleep.</h3>

<sub>FEATURED IN</sub><br>
<a href="https://www.businessinsider.com/aihawk-applies-jobs-for-you-linkedin-risks-inaccuracies-mistakes-2024-11"><b>Business Insider</b></a> ·
<a href="https://techcrunch.com/2024/10/10/a-reporter-used-ai-to-apply-to-2843-jobs/"><b>TechCrunch</b></a> ·
<a href="https://www.semafor.com/article/09/12/2024/linkedins-have-nots-and-have-bots"><b>Semafor</b></a> ·
<a href="https://www.wired.it/article/aihawk-come-automatizzare-ricerca-lavoro/"><b>Wired</b></a> ·
<a href="https://www.theverge.com/2024/10/10/24266898/ai-is-enabling-job-seekers-to-think-like-spammers"><b>The Verge</b></a> ·
<a href="https://www.vanityfair.it/article/intelligenza-artificiale-candidature-di-lavoro"><b>Vanity Fair</b></a> ·
<a href="https://www.404media.co/i-applied-to-2-843-roles-the-rise-of-ai-powered-job-application-bots/"><b>404 Media</b></a>
</div>

---

## How it works

Give it your resume. The job search finds the postings that fit, you pick, and it fills in and sends each job application on the company's own form.

| Step | What happens |
|---|---|
| **1. Reads your resume** | Turns it into a search: the titles, the skills and the places that fit you. |
| **2. Searches** | Millions of postings of the last 30 days from 37 public sources, rebuilt every five hours, searched on your computer. |
| **3. Scores** | Asks of every posting, against your resume, one question: is it in line with you? How sure the answer is makes the score, from 1 to 5. 50 jobs to a page, the next one ready while you read this one. |
| **4. You pick** | Select the jobs you want and say yes once. |
| **5. Applies** | A real Firefox opens the company's form, fills every required field, attaches your CV and sends. |
| **6. Keeps the answer** | What each page said, with a screenshot, for every application. |

---

## Install

Windows, in PowerShell:

```powershell
irm https://raw.githubusercontent.com/feder-cr/Jobs_Applier_AI_Agent/main/run.ps1 | iex
```

Linux, and macOS (on a Mac AIHawk finds and scores the jobs, but cannot send applications yet: its browser has no Mac version):

```bash
curl -LsSf https://raw.githubusercontent.com/feder-cr/Jobs_Applier_AI_Agent/main/run.sh | sh
```

You need a key: [get one from OrcaRouter](https://www.orcarouter.ai/ref/ref_accd17afb78fc95ac4d7). Signing up is free, and a new account starts with no credit: add a few dollars there before the first start, or AIHawk cannot read your resume. An application costs a few cents.

The first start downloads about 250 MB and takes a minute or two; after that it opens in seconds, also without a connection. Nothing else needs installing first (on Linux, `curl`). The first application brings the browser, about 430 MB more, and opens it in a window: on Linux that needs a desktop.

---

[Sites](docs/sites.md) · [Configuration](docs/configuration.md) · [Writing a plugin](docs/plugins.md) · [Wiki](https://github.com/feder-cr/Jobs_Applier_AI_Agent/wiki) · [Contributing](CONTRIBUTING.md) · [Security](SECURITY.md) · [Changelog](CHANGELOG.md)

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="https://api.star-history.com/svg?repos=feder-cr/Jobs_Applier_AI_Agent&type=Date&theme=dark">
  <img alt="Star history of AIHawk" src="https://api.star-history.com/svg?repos=feder-cr/Jobs_Applier_AI_Agent&type=Date" width="100%">
</picture>
