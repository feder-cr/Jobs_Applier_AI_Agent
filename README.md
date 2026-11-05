<div align="center">
<picture>
  <source media="(max-width: 767px) and (prefers-color-scheme: dark)" srcset="https://raw.githubusercontent.com/feder-cr/Jobs_Applier_AI_Agent/main/assets/banner-phone-dark.gif">
  <source media="(max-width: 767px)" srcset="https://raw.githubusercontent.com/feder-cr/Jobs_Applier_AI_Agent/main/assets/banner-phone-light.gif">
  <source media="(prefers-color-scheme: dark)" srcset="https://raw.githubusercontent.com/feder-cr/Jobs_Applier_AI_Agent/main/assets/banner-dark.gif">
  <img alt="AIHawk, a short film. Your name typed into one form, then another, then the CV you just attached. Nobody wants to apply for a job. You want the job. 10,213,067 postings narrow to 25, each with its reasons. You pick, you say yes once, and a browser you can watch fills the form and sends it. An illustration, with invented companies and an invented person." src="https://raw.githubusercontent.com/feder-cr/Jobs_Applier_AI_Agent/main/assets/banner-light.gif" width="100%">
</picture>
<h3 align="center">Applying by hand takes days, and most postings do not fit you.<br>
AIHawk narrows 10 million of them down to the ones that fit your resume, and applies to those you pick, in a browser you can watch.</h3>

<sub>THE EARLIER AIHAWK WAS FEATURED IN</sub><br>
<a href="https://www.businessinsider.com/aihawk-applies-jobs-for-you-linkedin-risks-inaccuracies-mistakes-2024-11"><b>Business Insider</b></a> ·
<a href="https://techcrunch.com/2024/10/10/a-reporter-used-ai-to-apply-to-2843-jobs/"><b>TechCrunch</b></a> ·
<a href="https://www.semafor.com/article/09/12/2024/linkedins-have-nots-and-have-bots"><b>Semafor</b></a> ·
<a href="https://www.wired.it/article/aihawk-come-automatizzare-ricerca-lavoro/"><b>Wired</b></a> ·
<a href="https://www.theverge.com/2024/10/10/24266898/ai-is-enabling-job-seekers-to-think-like-spammers"><b>The Verge</b></a> ·
<a href="https://www.vanityfair.it/article/intelligenza-artificiale-candidature-di-lavoro"><b>Vanity Fair</b></a> ·
<a href="https://www.404media.co/i-applied-to-2-843-roles-the-rise-of-ai-powered-job-application-bots/"><b>404 Media</b></a>
</div>

<br>

## How it works

A job search asks two questions, and AIHawk answers both.

**1. Which jobs fit you?** It narrows about 10.2 million postings, from 24 public sources rebuilt daily, down to the few worth reading.

- Your resume becomes a search plan, a graph of the words your jobs use, each with a weight. 600 postings fit it by their titles, found on your computer with no model.
- [Jev](https://github.com/feder-cr/jev), a decision model, reads 100 of them against your resume.
- A chat model scores the best 25 from 1 to 5, with the gaps and the reasons.

**2. Can it get through the form?** You select the jobs and say yes once.

- The model fills every required field, from what your resume says and what it implies, and attaches your CV.
- It drives [invisible-playwright-mcp](https://github.com/feder-cr/invisible_playwright_mcp), a real Firefox with a person's hands, in a window you can watch.
- AIHawk clicks send, reads the page, and keeps what it said for each job.

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

It installs what is missing, updates AIHawk and starts it. It is the same command every time. You need a key: [get one from OrcaRouter](https://www.orcarouter.ai/ref/ref_accd17afb78fc95ac4d7).

---

## Usage

The screen asks for your key and your resume, then shows the jobs, best first.

| Key | What it does |
|---|---|
| `enter` | why this score: the gaps and the reasons |
| `space` | select a job |
| `a` | send the selected jobs, after one yes |
| `s` | every application, and what each page said |
| `m` | score the next ones |
| `e` | settings: resume, sign-in to your mail and job sites, what to look for, where, models |

---

## Measured

| What | Result |
|---|---|
| Ranking, graded blind on real postings | nDCG@10 of 0.683, against 0.569 before Jev; 0.4 wrong jobs in the top 10 |
| Form model, on a local course of hard forms, of 100 | DeepSeek v4.1 flash 87, GLM 83, Gemini 81 |
| A form, on real sites | 0.4 to 3.3 cents in model calls; 3 to 27 minutes |

---

## Before you send

- **It goes out in your name, unread, after one yes.** Where your resume says nothing, the model writes "Please see my attached CV", picks the closest option or a plausible number, and ticks the boxes the form requires, statements such as the right to work included. You answer for all of it. Sites have terms of their own, and some do not allow this.
- **Nothing checks what the model does in the browser.** It is told to leave the send to AIHawk, and it could send early or half filled: [SECURITY.md](SECURITY.md).
- **Your data.** Your CV, the postings and the pages, screenshots included, go to OrcaRouter and its providers, on your paid key. The rest stays in `~/.aihawk`, sign-ins included.
- **Downloads.** About 740 MB of job index on the first search, and about 268 MB of browser on the first form. Windows and Linux: the browser has no macOS build.
- **The job data is not ours.** Each posting belongs to its employer. Some sources state no licence, and one asks not to be republished as listings; the published database includes it: [every source](docs/data-sources.md).

---

## Documentation

[Configuration](docs/configuration.md) · [Sites](docs/sites.md) · [Writing a plugin](docs/plugins.md) · [Data sources](docs/data-sources.md) · [Wiki](https://github.com/feder-cr/Jobs_Applier_AI_Agent/wiki) · [Contributing](CONTRIBUTING.md) · [Security](SECURITY.md) · [Changelog](CHANGELOG.md)

## Related projects

- **[invisible-playwright-mcp](https://github.com/feder-cr/invisible_playwright_mcp)** - the browser AIHawk drives, as an MCP server for any assistant.
- **[invisible_playwright](https://github.com/feder-cr/invisible_playwright)** - Playwright on an anti-detect Firefox, the engine underneath.
- **[Jev](https://github.com/feder-cr/jev)** - yes/no decisions on a laptop CPU.
- **[invisible_dots](https://github.com/feder-cr/invisible_dots)** - the former Dots project, once in this repository.

Built with [OrcaRouter](https://www.orcarouter.ai/ref/ref_accd17afb78fc95ac4d7). Its links here and on the screen are referral links: AIHawk gets 6% of what you spend through them.

---

## License

[MIT](LICENSE) for the code. See [NOTICE](NOTICE).

## Disclaimer

This project is provided as-is, with no warranties. Applications go out in your name, and you answer for them and for following each site's terms and the laws of your jurisdiction.
