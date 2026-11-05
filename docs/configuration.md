# Configuration

Press `e` in AIHawk to change these on the screen. AIHawk reads `aihawk.yaml` in its folder, `~/.aihawk` by default (see `--home`). A missing file, or a missing key, takes the default below. Restart `aihawk` after you edit the file.

Loading a resume rewrites this file, and the rewrite drops the comments you put in it, so keep your notes elsewhere.

| Key | Default | What it does |
|---|---|---|
| `name` | empty | Your name for this search, shown at the top of the list. |
| `goals` | empty | What you are looking for, in your own words. The decision model and the judge read it. |
| `database` | `https://github.com/feder-cr/Jobs_Applier_AI_Agent_AIHawk/releases/download/jobs-latest/manifest.json` | Where the job database is: the URL of its `manifest.json`, or the path of a local one. The files it names sit beside it, unless its `base_url` says otherwise. |
| `max_age_days` | `60` | Postings posted more than this many days ago are left out. A posting with no date is kept. |
| `model` | `deepseek/deepseek-v4.1-flash` | Writes the tailored CV and fills the application forms. |
| `quick_model` | `google/gemini-3.5-flash` | Writes the search plan from your CV, and the other short calls of a search. |
| `judge_model` | `deepseek/deepseek-v4.1-flash` | Writes the score and the report of each shortlisted posting. |
| `decision_model` | `typesafe/jev-1.14` | Jev, which answers the six questions that shortlist the postings. |
| `base_url` | `https://api.orcarouter.ai/v1` | The gateway every model call goes to. It must serve OpenAI's chat API, and `/systemone` for the decision model. |
| `api_key_env` | `ORCAROUTER_API_KEY` | The environment variable that holds the key. A key in it wins over the key file. |
| `filters.include` | empty | Title words to search for. With no `plan`, each word is one title. AIHawk asks for a resume until there is a CV and at least one of these. |
| `filters.exclude` | empty | Whole words: a posting whose title has one of them is left out. |
| `filters.locations` | empty | Places a posting's location must name, as whole words. |
| `filters.countries` | empty | Countries. A posting in one of them passes. With `locations`, a posting that names one of those places passes only if its country is empty or one of these. |
| `filters.cities` | empty | Cities. A posting whose location names one ranks higher; others are not left out. |
| `filters.plan` | empty | The title plan, with `nodes` and `arcs`. Each node has an `id`, a `weight` and `forms`, the wordings with their weights; a node of weight 0 only conditions the arcs. Each arc has `when` or `unless` (a node or a phrase in the title) and `then`, a list of `target` and `multiplier`; the target `*` is the whole title. Empty: the plan is `filters.include`. |
