# Swarm Studio screenshots

Taken in the Claude browser pane at a 1440x900 viewport (saved at 800x500).

- **`scripted-*` (2026-11-01, light theme):** the offline test run. The planner reads
  `tests/studio/todo_plan.json`, and the worker is the scripted `tests/agents/solver.py`
  (no model).
- **`real-*` (2026-11-02, dark theme):** the real run.
  - Chief: qwen2.5-coder:14b in Ollama.
  - Worker: `studio-14b` (Aider, architect mode, same model).
  - Hardware: GTX 1080 Ti.

## Offline run (scripted planner and worker)

| File | What it shows |
|---|---|
| `scripted-01-plan.jpg` | The plan artifact after the first chat message: routes, data, the worked example, and the hidden checks (folded) |
| `scripted-02-building.jpg` | Just after Approve: task 01 running, its live log, and the preview with "not built yet" sections |
| `scripted-03-review-log.jpg` | Two tasks passed. The log shows the attempt's checks, then the review (`integrate`: every gate plus the hidden tests on the merge) |
| `scripted-04-done.jpg` | All 3 features passed, and the preview shows the finished page |
| `scripted-05-preview-todos.jpg` | Using the built app in the preview: two todos added, counts updated |
| `scripted-06-diff.jpg` | A todo ticked off in the preview, and the Diff tab for task 01 |
| `scripted-07-attempt-prompt.jpg` | Attempts, then `prompt.md`: the exact prompt the worker got |
| `scripted-08-failed-task.jpg` | The failure path (a worker that changes nothing): 4 attempts, the build pauses, and Retry/Skip appear in the chat and the task |

## Real run (local 14B chief and worker)

| File | What it shows |
|---|---|
| `real-01-planning.jpg` | The request is sent and the chief starts planning |
| `real-02-chief-progress.jpg` | Chief progress in the top bar ("examples for todo_stats (3/3)") |
| `real-03-plan.jpg` | Plan v1 from the 14B: 3 features, 0 problems, Approve enabled (about 3 min, model load included) |
| `real-04-build-started.jpg` | Approved: the stubs and page are in the app, and task 01 is checking the base |
| `real-05-live-model-log.jpg` | The live log streams the model's transcript: the code it wrote, then "Applied edit" |
| `real-06-first-feature-passed.jpg` | Task 01 passed on its first attempt (73 s), was reviewed and merged, and the preview shows the live list |
| `real-07-retry-attempt3.jpg` | Task 02, attempt 3 of 4. Each attempt is a new Aider run: repo map off, the stub editable, the frame API read-only |
| `real-08-task-failed-paused.jpg` | Task 02 failed all 4 attempts and the build paused, with Retry/Skip in the chat. The hidden tests passed from attempt 1; the CONTRACT check failed (the model deleted the docstring, then moved it below the imports) |
| `real-09-retry-passed-review.jpg` | After the frame fix (an autofix restores the CONTRACT, and the studio updated the app's frame on restart), the retry of 02 passed on attempt 1. The review merged both branches and reran both hidden suites |
| `real-10-build-done.jpg` | Build finished: 3/3 features, each passed on the first attempt of its final run |
| `real-11-built-app-in-preview.jpg` | The app the 14B built, used in the preview: two todos added, one ticked off, counts 2 / 1 / 1 |
| `real-12-diff-and-remove.jpg` | One todo removed (counts 1 / 1 / 0), and the Diff of task 03: the 14B's `get_todo_stats` |
| `real-13-desktop-window.png` | The native desktop window (pywebview on WebView2), captured at full size (1500x950). The preview's data reset because the store is in memory |

## The real run in numbers (2026-11-02, GTX 1080 Ti)

| Step | Time | Result |
|---|---|---|
| Chief plan (outline + 3 scenario calls, model load included) | about 3 min | v1, 0 problems, 9 hidden scenarios |
| 01-todos | 73 s | passed, attempt 1 |
| 02-todo-edit, first run | 5 min 33 s | failed 4/4: CONTRACT check only (hidden tests 3/3 from attempt 1) |
| Frame fix + restart | (outside the run) | `tools/restore_contracts.py` autofix; frame update committed in the app |
| 02-todo-edit, retry | 83 s | passed, attempt 1 |
| 03-todo-stats | 62 s | passed, attempt 1 |

Every feature commit is by `swarm-agent` (the local 14B through the harness). No model code was edited by hand.
