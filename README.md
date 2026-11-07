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
| **2. Searches** | 10 million postings from 24 public sources, rebuilt every five hours, searched on your computer. |
| **3. Scores** | Reads the best 100 against your resume, then scores the top 25 from 1 to 5, with the gaps and the reasons. |
| **4. You pick** | Select the jobs you want and say yes once. |
| **5. Applies** | A real Firefox opens the company's form, fills every required field, attaches your CV and sends. |
| **6. Keeps the answer** | What each page said, with a screenshot, for every application. |

## Why AIHawk

- **The company's own form.** No LinkedIn account and no Easy Apply: it applies where the company takes applications.
- **Every posting is tried.** There is no list of sites it refuses, and a site can be taught in one Markdown file: [what has been confirmed](docs/sites.md).
- **A browser built to look like yours.** [invisible-playwright-mcp](https://github.com/feder-cr/invisible_playwright_mcp) is a real Firefox with a person's hands, in a window you can watch.
- **One command.** It installs what it needs, and everything else happens on the screen.

---

## Install

Windows, in PowerShell:

```powershell
irm https://raw.githubusercontent.com/feder-cr/Jobs_Applier_AI_Agent/main/run.ps1 | iex
```

Linux:

```bash
curl -LsSf https://raw.githubusercontent.com/feder-cr/Jobs_Applier_AI_Agent/main/run.sh | sh
```

You need a key: [get one from OrcaRouter](https://www.orcarouter.ai/ref/ref_accd17afb78fc95ac4d7).


---

[Sites](docs/sites.md) · [Configuration](docs/configuration.md) · [Writing a plugin](docs/plugins.md) · [Wiki](https://github.com/feder-cr/Jobs_Applier_AI_Agent/wiki) · [Contributing](CONTRIBUTING.md) · [Security](SECURITY.md) · [Changelog](CHANGELOG.md)

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="https://api.star-history.com/svg?repos=feder-cr/Jobs_Applier_AI_Agent&type=Date&theme=dark">
  <img alt="Star history of AIHawk" src="https://api.star-history.com/svg?repos=feder-cr/Jobs_Applier_AI_Agent&type=Date" width="100%">
</picture>
