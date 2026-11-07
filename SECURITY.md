# Security

## Report a problem privately

Do not open a public issue or a pull request for a security problem. Report it privately with GitHub's private vulnerability reporting:

1. Open the repository's **Security** tab.
2. Choose **Report a vulnerability**. Direct link: https://github.com/feder-cr/Jobs_Applier_AI_Agent/security/advisories/new

Say what you found, how to reproduce it, and what you think a person could lose. Do not include a real person's CV, e-mail or sign-in, or a working API key. Replace them with invented ones.

No response time is promised. The report stays private until it is fixed or until you and the maintainer agree on what to publish.

## What matters most here

AIHawk reads job pages written by strangers, fills their forms, and sends applications for a person. The problems that matter most are these:

1. **A posting's page steering the model.** The page's text, scripts and hidden fields reach the model as the content of tool results. Anything that lets a page make the model fill something the person did not give, click a control outside the form, or visit a site the plugin does not name is a serious problem.
2. **Sending without the person's yes.** The model is told to leave the send to AIHawk, which clicks after the person selects the jobs and presses `a` and Enter. Nothing checks the model's own calls, so a way to make it send more often than that matters most.
3. **Reading mail.** The mail plugins (`gmail.md`, `outlook.md`) tell the model to read only: a code or a link from the site the person applied to, received in the last few minutes. That is an instruction, not a guarantee, and nothing in the code holds it to that.
4. **Keys and sign-ins.** See below.

## Where keys and sign-ins are kept

- The OrcaRouter key comes from the `ORCAROUTER_API_KEY` environment variable, or from a file named `key` in the AIHawk folder.
- The AIHawk folder is `~/.aihawk`. It also holds the CV, the answers and the account password (in clear, in the `answers` table of `data/jobs.db`), the job data, what was sent (`applied.csv` and `data/proofs/`) and the browser profile (`browser-profile/`), which keeps the person's sign-ins.
- Do not paste a key, a browser profile or a folder from the AIHawk folder into an issue or a pull request.

## Known and accepted

- **Nothing checks what the model does in the browser.** It is told not to send and to stay on the form, and a click, Enter, a script or a visit elsewhere goes through. It can send a form before the person's yes or half filled, and a page that addresses it could try to steer it. Report a case where that happens.
- **The model fills every required field.** It takes what the CV and the answers say and works out what they imply, and where they give nothing it writes to see the attached CV, picks the closest option or a plausible number, and ticks the boxes the form requires, statements included. These go out in the person's name, unread, after the one yes.
- **The mail plugins read the mailbox that the browser is signed into.** That is how a code or a link is taken from the mail. It is limited by the instructions in those plugins, not enforced by the browser or by the code.
- **A captcha or a sign-in is not bypassed.** The model reports the form as blocked, and the person deals with it.
- **The model is a third-party service.** Everything sent to it, including the CV and what the pages say, leaves the machine. The model's output is not trusted: the person selects, and the send is checked by AIHawk.

## Before you send

- **It goes out in your name, unread.** Where your resume says nothing, the model writes "Please see my attached CV", picks the closest option or a plausible number, and ticks the boxes the form requires, the right to work included. You answer for all of it, and some sites do not allow this.
- **Nothing checks the model in the browser.** It could send early or half filled: [SECURITY.md](SECURITY.md).
- **Your data.** Your CV and the pages go to OrcaRouter and its providers. The rest stays in `~/.aihawk`.
- **Downloads.** About 740 MB of job index and 268 MB of browser. Windows and Linux.
- **The job data is not ours.** Some sources state no licence, and one asks not to be republished: [every source](docs/data-sources.md).
