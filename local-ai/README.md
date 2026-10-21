# aider-local: a local AI coding fallback

When your Claude usage runs out, keep coding with **Aider** (the hands: it reads your repo and edits
files) driven by a **local model on your GPU** (the brain), served by Ollama. No internet and no
usage limits. A local model is much weaker than Claude, though, so give it small, well-defined jobs.

## Use it

In any git repository:

```
aider-local                          # chat with the default brain (14b, architect mode)
aider-local src\app.py               # start with a file already in the chat
aider-local --normal                 # skip the planning pass: faster, less careful
aider-local --brain 7b               # pick a different brain for this session
aider-local --list                   # which brains exist and which are downloaded
aider-local --message "fix the typo in README.md" README.md   # one-shot, no chat
```

By default the brain works in **architect mode**: it first explains the change in plain words,
then a second pass makes the edit. That doubled-up thinking won one extra benchmark task for both
Qwen models. Use `--normal` for trivial edits where speed matters more.

Anything that isn't `--brain`, `--hand`, `--normal` or `--list` goes straight to Aider, so all of
[Aider's options](https://aider.chat/docs/config/options.html) work.

Inside the chat, the most useful Aider commands are:
- `/add <file>`: let the AI edit a file
- `/drop <file>`: remove a file from the chat (frees up context)
- `/undo`: revert the AI's last commit
- `/diff`: show the last change
- `/ask <question>`: ask without editing
- `/clear`: forget the conversation

Aider commits each change it makes, so `/undo` or `git revert` is always available.

## Brains (GTX 1080 Ti, 11 GB)

| Name | Ollama model | Size | Speed | Fits on GPU | Context | Benchmark (normal / architect) |
|---|---|---|---|---|---|---|
| **`14b`** (default) | qwen2.5-coder:14b | 9.0 GB | ~17 tokens/s | 88% (rest on CPU) | 8k | 2/4 / **3/4** |
| `7b` | qwen2.5-coder:7b | 4.7 GB | ~49 tokens/s | 100% | 16k | 1/4 / 2/4 |
| `16b` | deepseek-coder-v2:16b | 8.9 GB | ~48 tokens/s | 82% (rest on CPU) | 8k | 1/4 / 0/4 |

- **`14b`** is the most capable. A small edit takes ~40 s in architect mode.
- **`7b`** is 3× faster: use it for quick, simple edits, or when you need its bigger 16k context
  (many files in the chat).
- **`16b`** is fast but ignores instructions (it added a third-party package when told "standard
  library only"). Not recommended.

The benchmark is 4 small Python tasks, one run each (details in the main `README.md` → Results).
The default is set by `$DefaultBrain` and `$DefaultArchitect` near the top of `aider-local.ps1`,
or for one terminal with `$env:AIDER_LOCAL_BRAIN = "7b"`.

The first request after switching brains takes 1–2 minutes: the model loads from the E: hard drive.
It then stays loaded for 5 minutes after the last request.

## Getting good results from a small model

- **Add only the files that matter** (`/add`). Small models get confused by large contexts, and
  the 14b/16b only have an 8k-token window.
- **One small change per request.** "Add input validation to `parse_date`" works; "refactor the
  app" doesn't.
- **Say exactly what you want**, including edge cases and expected values.
- **Run your tests yourself** (`/run pytest`, or `/test` with `--test-cmd`) and paste failures back.
- If it repeats the same wrong fix, `/clear` and rephrase. The benchmark showed small models
  often resubmit identical wrong code; saying what's wrong with the last attempt helps.
- Inside the chat, `/ask` makes it explain first without editing; `/code` makes a single quick edit.

## How it's set up

- `aider-local.ps1`: the launcher. It starts Ollama if needed (the Ollama desktop app
  doesn't start its server reliably on this machine), checks the model is downloaded, and runs
  Aider with: English replies (Aider otherwise follows the Windows locale and asks for Thai), no
  analytics, no update prompts, and `model.settings.yml`.
- `model.settings.yml`: per-model context size (`num_ctx`) and reply cap (`num_predict`). The cap
  stops a looping model from generating for 10+ minutes.
- `install.ps1`: creates `%USERPROFILE%\.local\bin\aider-local.cmd`, the shim that puts the
  `aider-local` command on PATH, pointing at wherever this repo is cloned. Run it once per PC:
  `powershell -ExecutionPolicy Bypass -File local-ai\install.ps1`.

These settings live in the launcher on purpose, **not** in a global `~/.aider.conf.yml`: Aider
always reads that file, so it would also change the `swarm` benchmark harness's Aider runs. Plain
`aider` stays unchanged.

Models are stored in `E:\Playground_Projects\04_AgentSwarm\models` (see the main README).
Add a brain with `ollama pull <tag>`, then add it to `$Brains` in `aider-local.ps1` and to
`model.settings.yml`.
