<div align="center">
<picture>
  <source media="(max-width: 767px) and (prefers-color-scheme: dark)" srcset="https://raw.githubusercontent.com/feder-cr/Jobs_Applier_AI_Agent/main/assets/banner-phone-dark.gif">
  <source media="(max-width: 767px)" srcset="https://raw.githubusercontent.com/feder-cr/Jobs_Applier_AI_Agent/main/assets/banner-phone-light.gif">
  <source media="(prefers-color-scheme: dark)" srcset="https://raw.githubusercontent.com/feder-cr/Jobs_Applier_AI_Agent/main/assets/banner-dark.gif">
  <img alt="AIHawk, a short film. Your name typed into one form, then another, then the CV you just attached. Nobody wants to apply for a job. You want the job. 10,213,067 postings narrow to 25, each with its reasons. You pick, you say yes once, and a browser you can watch fills the form and sends it. An illustration, with invented companies and an invented person." src="https://raw.githubusercontent.com/feder-cr/Jobs_Applier_AI_Agent/main/assets/banner-light.gif" width="100%">
</picture>
<h3 align="center">Other job bots apply to everything.<br>
This one applies where you fit.</h3>

<sub>THE EARLIER AIHAWK WAS FEATURED IN</sub><br>
<a href="https://www.businessinsider.com/aihawk-applies-jobs-for-you-linkedin-risks-inaccuracies-mistakes-2024-11"><b>Business Insider</b></a> ·
<a href="https://techcrunch.com/2024/10/10/a-reporter-used-ai-to-apply-to-2843-jobs/"><b>TechCrunch</b></a> ·
<a href="https://www.semafor.com/article/09/12/2024/linkedins-have-nots-and-have-bots"><b>Semafor</b></a> ·
<a href="https://www.wired.it/article/aihawk-come-automatizzare-ricerca-lavoro/"><b>Wired</b></a> ·
<a href="https://www.theverge.com/2024/10/10/24266898/ai-is-enabling-job-seekers-to-think-like-spammers"><b>The Verge</b></a> ·
<a href="https://www.vanityfair.it/article/intelligenza-artificiale-candidature-di-lavoro"><b>Vanity Fair</b></a> ·
<a href="https://www.404media.co/i-applied-to-2-843-roles-the-rise-of-ai-powered-job-application-bots/"><b>404 Media</b></a>
</div>

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

The same command starts and updates it. You need a key: [get one from OrcaRouter](https://www.orcarouter.ai/ref/ref_accd17afb78fc95ac4d7).

---

## How it works

1. **It finds the jobs.** About 10 million postings, rebuilt daily, narrowed to your resume on your computer. [Jev](https://github.com/feder-cr/jev) reads the best 100.
2. **It scores them.** The best 25, from 1 to 5, with the gaps and the reasons.
3. **You say yes once.** It fills the forms you picked, attaches your CV and sends, in [a Firefox](https://github.com/feder-cr/invisible_playwright_mcp) you can watch.

---

## Usage

| Key | What it does |
|---|---|
| `enter` | why this score |
| `space` | select a job |
| `a` | send the selected jobs, after one yes |
| `s` | every application, and what each page said |
| `m` | score the next ones |
| `e` | settings |

---

## Before you send

- **It goes out in your name, unread.** Where your resume says nothing, the model writes "Please see my attached CV", picks the closest option or a plausible number, and ticks the boxes the form requires, the right to work included. You answer for all of it, and some sites do not allow this.
- **Nothing checks the model in the browser.** It could send early or half filled: [SECURITY.md](SECURITY.md).
- **Your data.** Your CV and the pages go to OrcaRouter and its providers. The rest stays in `~/.aihawk`.
- **Downloads.** About 740 MB of job index and 268 MB of browser. Windows and Linux.
- **The job data is not ours.** Some sources state no licence, and one asks not to be republished: [every source](docs/data-sources.md).

---

[Sites](docs/sites.md) · [Configuration](docs/configuration.md) · [Writing a plugin](docs/plugins.md) · [Wiki](https://github.com/feder-cr/Jobs_Applier_AI_Agent/wiki) · [Contributing](CONTRIBUTING.md) · [Changelog](CHANGELOG.md)

Built with [OrcaRouter](https://www.orcarouter.ai/ref/ref_accd17afb78fc95ac4d7). Its links here and on the screen are referral links: AIHawk gets 6% of what you spend through them.

[MIT](LICENSE) for the code, see [NOTICE](NOTICE). Provided as-is, with no warranties. Applications go out in your name, and you answer for them and for following each site's terms and the laws of your jurisdiction.
