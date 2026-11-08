-- The key a posting is known by, from its link: the same posting under two links has one key. The pipeline merges the
-- sources by it, and AIHawk stores every job under it; both read this one file, so they never disagree.

-- The link without its scheme, www, tracking parameters, query, fragment and a trailing /apply or /application.
CREATE OR REPLACE MACRO posting_link(url) AS lower(regexp_replace(regexp_replace(regexp_replace(
    url, '^https?://(www\.)?', ''),
    '[?&](utm_[a-z]+|gh_src|source|src|ref|lever-[a-z-]+|trk|jobPipeline)=[^&#]*', '', 'g'),
    '(/apply|/application|/)?([?#].*)?$', '\2'));

-- Workday serves one posting under its company's career sites, with or without the language or the city in the path, and
-- on myworkdaysite.com too: the company and the requisition id at the end are the posting. SmartRecruiters serves one
-- posting under jobs., careers. and api., with a slug and a query that vary: its numeric id is the posting. Greenhouse
-- serves one posting from several hosts.
CREATE OR REPLACE MACRO posting_key(url) AS CASE
    WHEN regexp_matches(posting_link(url), '^[a-z0-9-]+\.wd[0-9]+\.myworkdayjobs\.com/.*_[^_/]+$')
        THEN 'workday:' || regexp_extract(posting_link(url), '^([a-z0-9-]+)\.', 1) || ':' || regexp_extract(posting_link(url), '_([^_/]+)$', 1)
    WHEN regexp_matches(posting_link(url), '^wd[0-9]+\.myworkdaysite\.com/(.*/)?recruiting/[^/]+/.*_[^_/]+$')
        THEN 'workday:' || regexp_extract(posting_link(url), 'recruiting/([^/]+)/', 1) || ':' || regexp_extract(posting_link(url), '_([^_/]+)$', 1)
    WHEN regexp_matches(posting_link(url), '^(jobs|careers|api)\.smartrecruiters\.com/(.*/)?[0-9]{12,}(-|/|$)')
        THEN 'smartrecruiters:' || regexp_extract(posting_link(url), '/([0-9]{12,})(-|/|$)', 1)
    ELSE regexp_replace(posting_link(url), '^(job-boards|boards)(\.eu)?\.greenhouse\.io/', 'greenhouse.io/')
END;
