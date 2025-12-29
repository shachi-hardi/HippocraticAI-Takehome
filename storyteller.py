import os
import json
import re
import openai
from dataclasses import dataclass
from typing import Dict, List, Any, Optional


"""
Before submitting the assignment, describe here in a few sentences what you would have built next if you spent 2 more hours on this project:

With additional time, I would focus on improving the user experience by adding a small set of predefined story arc templates (such as cozy bedtime, gentle adventure, or mystery) so users get more consistent results without extra complexity. 
I would also add lightweight validation and testing to ensure the judge reliably enforces age-appropriateness and safety.
I would add simple development logging of judge scores to make it easier to iterate on story quality and prompt tuning.
I’d keep a handful of sample prompts and rerun them whenever I change the prompts or logic, just to make sure the output quality and safety don’t accidentally get worse.
"""


def call_model(prompt: str, max_tokens=3000, temperature=0.6) -> str:
    key = os.getenv("OPENAI_API_KEY", "")
    openai.api_key = key.strip() if isinstance(key, str) else key
    if not openai.api_key:
        raise RuntimeError("OPENAI_API_KEY is not set. Set it in your environment and rerun.")

    resp = openai.ChatCompletion.create(
        model="gpt-3.5-turbo",
        messages=[{"role": "user", "content": prompt}],
        stream=False,
        max_tokens=max_tokens,
        temperature=temperature,
    )
    return resp.choices[0].message["content"]


@dataclass
class ParsedRequest:
    raw_request: str
    title_hint: str
    characters: List[str]
    setting: str
    theme: str
    tone: str
    constraints: List[str]


@dataclass
class ArcPlan:
    target_words: int
    beats: List[str]  # exactly 6 beats


@dataclass
class JudgeResult:
    overall_pass: bool
    scores: Dict[str, int]
    strengths: List[str]
    issues: List[str]
    fixes: List[str]


# prompt builders
def parser_prompt(user_request: str) -> str:
    return f"""
You extract structured requirements for a bedtime story appropriate for ages 5–10.

User request:
{user_request}

Return ONLY valid JSON with keys:
- title_hint
- characters (list)
- setting
- theme
- tone
- constraints (list)

Constraints must include:
"no gore", "no explicit romance", "no cruelty", "no graphic violence", "warm ending", "bedtime pacing".
"""


def arc_planner_prompt(user_request: str) -> str:
    return f"""
You plan a simple bedtime story arc for ages 5–10.

User request:
{user_request}

IMPORTANT:
- Keep the core idea of the user request.
- If the request includes unsafe topics (like "robber"), do NOT replace it with an unrelated job.
  Reframe it gently (pretend play, misunderstanding, learning honesty, returning items).

Return ONLY valid JSON with keys:
- target_words: integer 600 to 1100
- beats: list of EXACTLY 6 beats in order:
  1) Hook (introduce characters + cozy setting)
  2) Small Problem (kid-safe)
  3) Attempt 1
  4) Attempt 2
  5) Gentle Climax (safe, not scary)
  6) Warm Ending (calm, bedtime-ready)
"""


def storyteller_prompt(parsed: ParsedRequest, plan: ArcPlan) -> str:
    constraints = "\n".join(f"- {c}" for c in parsed.constraints)
    characters = ", ".join(parsed.characters) if parsed.characters else "Invent two lovable characters"
    beats_text = "\n".join(f"{i+1}. {b}" for i, b in enumerate(plan.beats))

    return f"""
Write a bedtime story for ages 5–10.

User request (keep the core idea; reframe gently if needed, do NOT replace it):
{parsed.raw_request}

Title idea: {parsed.title_hint}
Characters: {characters}
Setting: {parsed.setting}
Theme: {parsed.theme}
Tone: {parsed.tone}

Constraints:
{constraints}

Story arc beats (follow these in order):
{beats_text}

Writing guidelines:
- Clear, natural language suitable for ages 5–10 (no toddler phrasing)
- Treat the reader as a curious child, not a toddler
- Calm, comforting bedtime tone without sounding childish
- Emotionally warm but not simplistic
- Light, age-appropriate humor is welcome

Story requirements:
- Clear arc: setup → problem → gentle resolution
- Cozy bedtime tone
- Simple language
- 6–10 short paragraphs
- Warm, calming ending

OUTPUT RULES:
- Output ONLY the story (title + paragraphs)
- No commentary, no apologies, no extra explanation, no quotes around the story

Length: about {plan.target_words} words.

Begin the story now.
"""


def judge_prompt(user_request: str, story: str) -> str:
    return f"""
You are a judge evaluating a bedtime story for ages 5–10.

User request:
{user_request}

Story:
\"\"\"{story}\"\"\"

Score each from 0–10:
- age_appropriateness
- coziness
- story_arc
- character_warmth
- language_simplicity
- creativity
- safety

Return ONLY valid JSON with:
- overall_pass (boolean; pass if all >=7 and safety >=8)
- scores (object)
- strengths (list)
- issues (list)
- fixes (list of concrete improvements)

Do NOT rewrite the story.
"""


def reviser_prompt(user_request: str, story: str, fixes: List[str]) -> str:
    fixes_text = "\n".join(f"- {f}" for f in (fixes or ["Make it cozier and simpler."]))

    return f"""
Revise the bedtime story below for ages 5–10.

User request:
{user_request}

Original story:
\"\"\"{story}\"\"\"

Apply ONLY these fixes:
{fixes_text}

Keep character names and the overall structure.
Output ONLY the full revised story (no commentary, no quotes).
"""


def feedback_reviser_prompt(user_request: str, story: str, feedback: str) -> str:
    return f"""
Revise the bedtime story for ages 5–10 based on user feedback.

Original request:
{user_request}

User feedback:
{feedback}

Story:
\"\"\"{story}\"\"\"

Rules:
- Keep the same main characters and overall plot
- Output ONLY the revised story (no commentary, no quotes)

If feedback is about LENGTH:
- "shorter": reduce to 4–6 short paragraphs
- "longer": expand to 8–12 short paragraphs (add cozy details, not new major plot)

If feedback is about the ENDING (e.g., "different ending"):
- change ONLY the final paragraph and make it clearly different

Otherwise:
- apply the feedback as a tone/style change across the story.

Now output the full revised story.
"""


# helpers
def safe_json_loads(text: str) -> Optional[Dict[str, Any]]:
    try:
        return json.loads(text)
    except Exception:
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if match:
            try:
                return json.loads(match.group(0))
            except Exception:
                return None
    return None


def parse_request(user_request: str) -> ParsedRequest:
    raw = call_model(parser_prompt(user_request), temperature=0.2)
    data = safe_json_loads(raw) or {}

    constraints = data.get("constraints", [])
    required = [
        "no gore",
        "no explicit romance",
        "no cruelty",
        "no graphic violence",
        "warm ending",
        "bedtime pacing",
    ]
    for r in required:
        if r not in constraints:
            constraints.append(r)

    return ParsedRequest(
        raw_request=user_request,
        title_hint=data.get("title_hint", "A Cozy Bedtime Adventure"),
        characters=data.get("characters", []),
        setting=data.get("setting", "a quiet, magical place"),
        theme=data.get("theme", "friendship and kindness"),
        tone=data.get("tone", "cozy and gentle"),
        constraints=constraints,
    )


def make_arc_plan(user_request: str) -> ArcPlan:
    raw = call_model(arc_planner_prompt(user_request), temperature=0.2)
    data = safe_json_loads(raw) or {}

    target_words = int(data.get("target_words") or 900)
    beats = data.get("beats") or []

    if target_words < 600 or target_words > 1100:
        target_words = 900

    if len(beats) != 6:
        beats = [
            "Hook: Introduce the characters in a cozy place.",
            "Small Problem: A tiny kid-safe problem appears.",
            "Attempt 1: They try a simple solution.",
            "Attempt 2: They try a different solution.",
            "Gentle Climax: A small safe moment resolves the problem.",
            "Warm Ending: Calm wrap-up and bedtime-ready goodnight.",
        ]

    return ArcPlan(target_words=target_words, beats=beats)


def judge_story(user_request: str, story: str) -> JudgeResult:
    raw = call_model(judge_prompt(user_request, story), temperature=0.1)
    data = safe_json_loads(raw) or {}

    scores = data.get("scores", {})
    safety = scores.get("safety", 0)
    overall_pass = data.get("overall_pass", False)

    if not data.get("overall_pass"):
        overall_pass = all(v >= 7 for v in scores.values()) and safety >= 8

    return JudgeResult(
        overall_pass=overall_pass,
        scores=scores,
        strengths=data.get("strengths", []),
        issues=data.get("issues", []),
        fixes=data.get("fixes", []),
    )


def generate_story_with_judging(user_request: str, max_rounds: int = 3) -> str:
    parsed = parse_request(user_request)
    plan = make_arc_plan(user_request)

    story = call_model(storyteller_prompt(parsed, plan), temperature=0.8)

    for _ in range(max_rounds):
        verdict = judge_story(user_request, story)
        if verdict.overall_pass:
            return story
        story = call_model(
            reviser_prompt(user_request, story, verdict.fixes),
            temperature=0.7,
        )

    return story


# main + feedback loop
def main():
    user_input = input("What kind of story do you want to hear? ").strip()
    if not user_input:
        user_input = "A story about a girl named Alice and her best friend Bob, who happens to be a cat."

    story = generate_story_with_judging(user_input)
    print("\n" + story + "\n")

    feedback = input("Want changes? (shorter/funnier/more magical/different ending) Press Enter to keep: ").strip()
    if feedback:
        revised = call_model(feedback_reviser_prompt(user_input, story, feedback), temperature=0.7)
        print("\nREVISED STORY:\n")
        print(revised)
        print()


if __name__ == "__main__":
    main()
