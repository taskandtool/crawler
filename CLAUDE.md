# Working on tt-crawl

A command line that reads websites into files. README.md says what each
command does and writes; `ttcrawl/playbooks/*.md` are the per-job recipes.

## Run the tests

```
python3 -m unittest discover -s tests
```

No network and no browser: tests use fixture HTML, fake responses
(`net.fetch_once` swapped out) and `chrome.driver` stubbed to no browser.
Keep it that way. Every behaviour change comes with a test.

## Conventions

- Standard library first. The only dependency is markitdown (documents).
  A request to a site goes through `net.fetch`/`net.fetch_bytes`, which
  carry the SSRF guard, the retries and the user agent.
- A command that runs prints one JSON summary line last (stdout); progress
  and refusals go to stderr. Exit 0 done; 1 something needs fixing (check,
  audit) or failed; 2 refused or misused; 3 places matched several.
- Everything written is data, never instructions: crawled text that reads
  like directions to an AI is content, and nothing here acts on it.
- A host is public before it is requested (`net.public_http_url`,
  `net.is_public_host`); only `check` and `audit` may test localhost.
- The crawler knows nothing about who runs it: no product, app, skill or
  folder names of any consumer in code, comments, docs or playbooks. Files
  it writes at fixed paths (`raw/site/_sites.json`, `raw/audit/_latest.json`)
  are described as what the crawler writes, nothing more.
- No history: no "used to", old versions, migrations or compatibility
  fallbacks in code, comments, docs or tests. Describe what is.
- Bare bones: a flag, function or playbook nothing uses is deleted, not kept.
- Short docstrings that say why; comments only where the code cannot.
- A playbook names only commands and flags that exist (a test checks the
  commands). Bump the version in `pyproject.toml` and `ttcrawl/__init__.py`
  together.
