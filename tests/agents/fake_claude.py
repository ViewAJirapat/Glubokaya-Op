"""A stand-in for the `claude` CLI in `-p --output-format json` mode.

Answers come from the JSON list in the file $FAKE_CLAUDE_ANSWERS (one result object per
call, consumed in order); every call's argv and stdin are appended to $FAKE_CLAUDE_LOG.
"""

import json
import os
import sys


def main() -> int:
    answers_path = os.environ["FAKE_CLAUDE_ANSWERS"]
    with open(answers_path, encoding="utf-8") as fh:
        answers = json.load(fh)
    prompt = sys.stdin.read()
    with open(os.environ["FAKE_CLAUDE_LOG"], "a", encoding="utf-8") as fh:
        fh.write(json.dumps({"argv": sys.argv[1:], "stdin": prompt}) + "\n")
    if not answers:
        print("fake claude: no answer left", file=sys.stderr)
        return 1
    answer = answers.pop(0)
    with open(answers_path, "w", encoding="utf-8") as fh:
        json.dump(answers, fh)
    if isinstance(answer, str):  # raw output, for the "no JSON result" case
        print(answer)
        return 0
    print("some banner line the CLI may print")
    print(json.dumps(answer))
    return 0


if __name__ == "__main__":
    sys.exit(main())
