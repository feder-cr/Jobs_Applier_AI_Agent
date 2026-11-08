# Data sources

AIHawk's job database merges 24 public sources. `pipeline/build.py` reads each one every five hours, and the [feder-cr/jobs](https://huggingface.co/datasets/feder-cr/jobs) dataset on Hugging Face keeps the postings that survive the merge. Each posting names the sources that carried it.

The licence column says what the source's own page or dataset card states. "Not stated" means no licence was found on that page or in the repository's files. Nothing here is a legal opinion. Postings belong to their employers, and the link in each row goes to the page this table relies on. AIHawk's MIT licence covers its code, not this data.

The repository does not state how many postings each source gives. The total in [README.md](../README.md) is for the whole database.

| Source | What it is | Where the data comes from | Licence or terms | Page relied on |
|---|---|---|---|---|
| `jobhive` | Job postings in one Parquet file | `storage.stapply.ai/jobhive/v1/all.parquet`; code home `kalil0321/ats-scrapers` | Not stated for the data. The repository's code is MIT. | [ats-scrapers](https://github.com/kalil0321/ats-scrapers) |
| `riley` | Job postings from Riley-Dorrington's job-board aggregator | Release file `all_jobs.json.gz` of `Riley-Dorrington/job-board-aggregator` | Not stated for the release file. The repository's code is MIT, and its `data/` folder says CC BY-NC 4.0 for its curated company datasets. | [job-board-aggregator](https://github.com/Riley-Dorrington/job-board-aggregator) |
| `rolebase` | Job postings as one JSON Lines file per board | `chandranshB/roleBase`, `data/jobs/*.jsonl` | AGPL-3.0 (the repository's stated licence) | [roleBase](https://github.com/chandranshB/roleBase) |
| `scholarweave` | Jobs-Dataset-v2, active postings joined with companies | Hugging Face `scholarweave/Jobs-Dataset-v2` | Apache-2.0 | [dataset card](https://huggingface.co/datasets/scholarweave/Jobs-Dataset-v2) |
| `headcount-pay` | Pay-transparency job postings | Hugging Face `HeadcountLabs/pay-transparency-jobs` | ODbL 1.0 | [dataset card](https://huggingface.co/datasets/HeadcountLabs/pay-transparency-jobs) |
| `headcount-tech` | Tech job postings | Hugging Face `HeadcountLabs/tech-jobs` | ODbL 1.0 | [dataset card](https://huggingface.co/datasets/HeadcountLabs/tech-jobs) |
| `headcount-remote` | Remote job postings | Hugging Face `HeadcountLabs/remote-jobs` | ODbL 1.0 | [dataset card](https://huggingface.co/datasets/HeadcountLabs/remote-jobs) |
| `headcount-internships` | Internship postings | Hugging Face `HeadcountLabs/internships` | ODbL 1.0 | [dataset card](https://huggingface.co/datasets/HeadcountLabs/internships) |
| `headcount-healthcare` | Healthcare job postings | Hugging Face `HeadcountLabs/healthcare-jobs` | ODbL 1.0 | [dataset card](https://huggingface.co/datasets/HeadcountLabs/healthcare-jobs) |
| `eu-tech-jobs` | EU tech job postings, latest snapshot | Hugging Face `Aramente/eu-tech-jobs` | CC-BY-4.0 | [dataset card](https://huggingface.co/datasets/Aramente/eu-tech-jobs) |
| `minmaxxxer` | Job postings with salaries | Hugging Face `minmaxxxer/job-postings-with-salaries` | Its own `LICENSE`: research, analysis, teaching and model development; "Do not republish the postings as job listings of your own." | [LICENSE](https://huggingface.co/datasets/minmaxxxer/job-postings-with-salaries/blob/main/LICENSE) |
| `lookin4jobs` | Open job data in one JSON file | `lookin4jobs/open-jobs-data`, `data/jobs.json` | MIT (the repository's licence). The README says postings belong to their employers. | [open-jobs-data](https://github.com/lookin4jobs/open-jobs-data) |
| `conorscode` | Open job data in one JSON file | `ConorsCode/open-jobs-data`, `data/jobs.json` | MIT (the repository's licence). The README says postings belong to their employers. | [open-jobs-data](https://github.com/ConorsCode/open-jobs-data) |
| `fecklesspleb` | New jobs from ATS boards, in CSV | `fecklesspleb/ats-new-jobs-feed`, `data/jobs.csv` | MIT | [ats-new-jobs-feed](https://github.com/fecklesspleb/ats-new-jobs-feed) |
| `simplify-new-grad` | New-grad job listings | `SimplifyJobs/New-Grad-Positions`, `.github/scripts/listings.json` | Not stated. No LICENSE file was found. | [New-Grad-Positions](https://github.com/SimplifyJobs/New-Grad-Positions) |
| `simplify-internships` | Summer 2027 internship listings | `SimplifyJobs/Summer2027-Internships`, `.github/scripts/listings.json` | Not stated. No LICENSE file was found. | [Summer2027-Internships](https://github.com/SimplifyJobs/Summer2027-Internships) |
| `vanshb03-new-grad` | New-grad job listings for 2027 | `vanshb03/New-Grad-2027`, `.github/scripts/listings.json` | MIT (`LICENSE.txt`) | [New-Grad-2027](https://github.com/vanshb03/New-Grad-2027) |
| `vanshb03-internships` | Summer 2026 internship listings | `vanshb03/Summer2026-Internships`, `.github/scripts/listings.json` | MIT (`LICENSE.txt`) | [Summer2026-Internships](https://github.com/vanshb03/Summer2026-Internships) |
| `simplify-trabaho` | Job listings in `data/listings.json` | `yanicells/SimplifyTrabaho` | MIT for the code. The README says the dataset is CC0 1.0 (public domain). | [SimplifyTrabaho](https://github.com/yanicells/SimplifyTrabaho) |
| `headcount-engineering-interns` | Engineering internship postings, in CSV | `HeadcountLabs/engineering-internships`, `data/jobs.csv` | ODbL 1.0 for the job data (the repository's `LICENSE`). Job descriptions belong to their employers. Takedowns: [headcountlabs.com/remove](https://headcountlabs.com/remove/). | [engineering-internships](https://github.com/HeadcountLabs/engineering-internships) |
| `headcount-software-interns` | Software engineering internship postings, in CSV | `HeadcountLabs/software-engineering-internships`, `data/jobs.csv` | ODbL 1.0 for the list data. Headcount Labs' own values are under DbCL 1.0. Job descriptions belong to their employers. Takedowns: [headcountlabs.com/remove](https://headcountlabs.com/remove/). | [software-engineering-internships](https://github.com/HeadcountLabs/software-engineering-internships) |
| `applyguy` | 2027 new-grad jobs | `ApplyGuy/2027-New-Grad-Jobs`, `data/new-grad-jobs.json` | Not stated. No LICENSE file was found. | [2027-New-Grad-Jobs](https://github.com/ApplyGuy/2027-New-Grad-Jobs) |
| `fde-jobs` | Forward-deployed engineer job list, in CSV | `stephanelx88/forward-deployed-engineer-jobs`, `data/fde-jobs.csv` | CC BY 4.0 | [forward-deployed-engineer-jobs](https://github.com/stephanelx88/forward-deployed-engineer-jobs) |
| `remote-india` | Remote jobs open to India, in one JSON file | `raghavendra81422-boop/remote-jobs-open-to-india`, `data.json` | MIT (the repository's `LICENSE` file) | [LICENSE](https://raw.githubusercontent.com/raghavendra81422-boop/remote-jobs-open-to-india/HEAD/LICENSE) |

## Credit

Credit the sources ask for: job data from [Headcount Labs](https://headcountlabs.com), ODbL 1.0, with location data from OpenStreetMap contributors (ODbL) and Overture Maps; Forward Deployed Engineer jobs from FDE Times, CC BY 4.0.

## Removing a source

A source's owner can ask for it to be removed. There are two ways.

- **In public:** open an issue with the [data source template](../.github/ISSUE_TEMPLATE/data_source.md). Say which source it is, who you are, and what to remove.
- **In private:** if you would rather not write in public, use the private report in [SECURITY.md](../SECURITY.md).

What happens next: the source is removed from `SOURCES` in `pipeline/build.py` and from this table. The next build, within five hours, in [`.github/workflows/jobs-database.yml`](../.github/workflows/jobs-database.yml) does not read it. Builds already published keep their postings until the publish step drops them. It keeps the last six builds, about thirty hours.
