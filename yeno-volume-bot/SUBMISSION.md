# YeNo Volume Bot — submission checklist

Email `submit@builderr.ai` with the fields below.

| Field | Value |
|-------|-------|
| Bot name | _(choose one, e.g. "favourite-churn-v1")_ |
| Contact | _(your email)_ |
| Repository or `/decide` endpoint | _(repo URL, or an HTTPS `/decide` endpoint)_ |
| Immutable commit / container digest | _(fill after committing — `git rev-parse HEAD`)_ |
| Run command | `python server.py` (serves `POST /decide` on `127.0.0.1:8080`) |
| Models / APIs | **None.** Pure Python stdlib; no LLM, no external API, no network. |
| Expected external cost | **$0** — no third-party calls. |
| Language / runtime | Python 3.9+ (no dependencies). |

## Before you submit

1. `python -m unittest discover -s tests` → 16 passing.
2. `python server.py` then run the kit's `yeno-smoke-test.py` → `contractSmokePassed: true`.
3. **Recommended:** capture real observation frames and run
   `python replay.py --frames real.jsonl --sweep` to freeze the best `Config` in
   `bot.py`. The synthetic `simulator.py` validates correctness only, not volume.
4. Commit, then paste the commit hash above and in the email.
