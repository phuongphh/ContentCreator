import os
import sys
import anthropic
import requests

ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY")
GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN")
PR_NUMBER = os.environ.get("PR_NUMBER")
REPO = os.environ.get("REPO")

if not ANTHROPIC_API_KEY:
    print("ERROR: ANTHROPIC_API_KEY secret is not set.")
    sys.exit(1)

client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

try:
    with open("pr_diff.txt", "r") as f:
        diff = f.read().strip()
except FileNotFoundError:
    print("ERROR: pr_diff.txt not found. Diff step may have failed.")
    sys.exit(1)

if not diff:
    print("No diff found. Skipping review.")
    sys.exit(0)

SYSTEM_PROMPT = """
You are a strict code reviewer for a Vietnamese AI Content Pipeline system
("AI 5 Phút Mỗi Ngày" — daily AI news for Vietnamese office workers).

The project collects AI news from RSS/Twitter/Reddit/Product Hunt, filters
with keywords, scores with Claude Haiku, analyzes with Claude Sonnet, and
sends Telegram reports.

You must check:

1. Does the PR modify files outside the issue scope?
2. Does it introduce hardcoded API keys or secrets?
3. Does it break the pipeline's graceful error handling (each source should fail independently)?
4. Are new modules independently runnable (if __name__ == "__main__")?
5. Does it respect the cost budget (Haiku for scoring, Sonnet only for top 5 articles)?
6. Does it modify unrelated modules?
7. Are there any security issues (API key exposure, injection, etc.)?

IMPORTANT: The diff below only shows CHANGED lines. Functions, constants, and
imports that already exist in the codebase but are NOT modified will NOT appear
in the diff. Do NOT flag calls to functions or references to constants as
"undefined" if they simply don't appear in the diff — they likely already exist
in the codebase. Only flag something as undefined if the diff shows it being
NEWLY called in a NEWLY created file with no corresponding definition.

If any violation is found, respond with:
FAIL: <reason>

If everything is correct, respond with:
PASS
"""

# Collect full content of changed files for context
import subprocess
changed_files = subprocess.run(
    ["git", "diff", "--name-only", f"origin/{os.environ.get('GITHUB_BASE_REF', 'main')}...HEAD"],
    capture_output=True, text=True
).stdout.strip().split("\n")

file_contents = []
for fpath in changed_files:
    if fpath.endswith(".py") and os.path.exists(fpath):
        try:
            with open(fpath) as f:
                content = f.read()
            file_contents.append(f"=== FULL FILE: {fpath} ===\n{content}")
        except Exception:
            pass

context = "\n\n".join(file_contents)

user_message = f"""## DIFF (changes only):
{diff}

## FULL FILES (for context — to verify that called functions/constants exist):
{context}"""

# Truncate if too long for Haiku context
if len(user_message) > 80000:
    user_message = f"""## DIFF (changes only):
{diff}

(Full file context omitted due to size — assume existing functions/constants are defined.)"""

response = client.messages.create(
    model="claude-haiku-4-5-20251001",
    max_tokens=200,
    temperature=0,
    system=SYSTEM_PROMPT,
    messages=[
        {
            "role": "user",
            "content": user_message
        }
    ]
)

result = response.content[0].text.strip()

print(result)

# Post comment to PR if GitHub token and PR number are available
if GITHUB_TOKEN and PR_NUMBER and REPO:
    comment_body = f"## Code Review Result\n\n```\n{result}\n```"
    try:
        requests.post(
            f"https://api.github.com/repos/{REPO}/issues/{PR_NUMBER}/comments",
            json={"body": comment_body},
            headers={
                "Authorization": f"Bearer {GITHUB_TOKEN}",
                "Accept": "application/vnd.github+json",
            },
        )
    except Exception as e:
        print(f"Warning: Could not post PR comment: {e}")

if result.startswith("FAIL"):
    sys.exit(1)
