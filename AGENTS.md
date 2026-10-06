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
- Output goes through `say.py`, never a hand-rolled print: `say.command`
  makes a subcommand (description, output epilog, `--json`), `say.done`
  prints the result (text by default, ending in `Next:`; one JSON line with
  `--json`), `say.fail` a refusal on stderr ending in `Try:` (JSON under
  `--json`); a refusal prints nothing on stdout. Validate an argument with
  an argparse `type=`, so misuse exits 2 before any network or browser.
  `cli.main` turns an unexpected exception into one refusal (exit 1;
  `TTCRAWL_DEBUG=1` re-raises). `tests/test_cli_contract.py` holds every
  command to this. README.md "Output and exit codes" says the same for
  users; change both together.
- Exit 0 done; 1 failed, or something needs fixing (check, audit, setup);
  2 refused or misused; 3 places matched several; 130 interrupted. Wrong
  input (a missing folder, file or id) is never 0.
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
- A playbook names only commands and flags that exist: a test parses every
  `tt-crawl` line in the playbooks and README.md.
- Bump the version in `pyproject.toml` and `ttcrawl/__init__.py` together.
