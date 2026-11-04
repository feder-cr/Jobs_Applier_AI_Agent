# Security

## Report a problem privately

Do not open a public issue or a pull request for a security problem. Report it privately with GitHub's private vulnerability reporting:

1. Open the repository's **Security** tab.
2. Choose **Report a vulnerability**. Direct link: https://github.com/feder-cr/Jobs_Applier_AI_Agent_AIHawk/security/advisories/new

Say what you found, how to reproduce it, and what you think a person could lose. Do not include a real person's CV, e-mail or sign-in, or a working API key. Replace them with invented ones.

No response time is promised. The report stays private until it is fixed or until you and the maintainer agree on what to publish.

## What matters most here

AIHawk reads job pages written by strangers, fills their forms, and sends applications for a person. The problems that matter most are these:

1. **A posting's page steering the model.** The page's text, scripts and hidden fields reach the model as the content of tool results. Anything that lets a page make the model fill something the person did not give, click a control outside the form, or visit a site the plugin does not name is a serious problem.
2. **Sending without the person's yes.** An application or an e-mail goes out only after the person selects the jobs, presses `a` and Enter. Anything that sends one another way, or gets past the refusal in `src/aihawk/apply.py` that stops clicks and Enter from sending a form, matters most.
3. **Reading mail.** The mail plugins (`gmail.md`, `outlook.md`) allow reading only: a code or a link from the site the person applied to, received in the last few minutes. Anything that reads, sends, deletes or opens other mail matters.
4. **Keys and sign-ins.** See below.

## Where keys and sign-ins are kept

- The OrcaRouter key comes from the environment variable named by `api_key_env` in `aihawk.yaml` (default `ORCAROUTER_API_KEY`), or from a file named `key` in the AIHawk folder.
- The AIHawk folder is `~/.aihawk` by default, or the folder given by `--home` or the `AIHAWK_HOME` variable. It also holds the CV, the answers, the job data, what was sent (`applied.csv` and `data/proofs/`) and the browser profile (`browser-profile/`), which keeps the person's sign-ins.
- Do not paste a key, a browser profile or a folder from the AIHawk folder into an issue or a pull request.

## Known and accepted

- **A script can send a form.** As a last resort, the model may run a script in the page (`browser_evaluate`). The refusal in `apply.py` does not check a script, so a script can send a form without the person's yes. This is accepted on purpose: a script is the last rung, offered only when the other browser tools cannot do the job. The script is watched after each call. Report a case where a script sends something the person did not select as a vulnerability.
- **The mail plugins read the mailbox that the browser is signed into.** That is how a code or a link is taken from the mail. It is limited by the rules in those plugins, not by the browser.
- **A captcha or a sign-in is not bypassed.** The model reports the form as blocked, and the person deals with it.
- **The model is a third-party service.** Everything sent to it, including the CV and what the pages say, leaves the machine. The model's output is not trusted: the person selects, and the send is checked by AIHawk.
