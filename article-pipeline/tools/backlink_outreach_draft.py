"""Drafts an outreach email for one backlink_candidates row - never sends
it. The draft is meant to be copied out of the dashboard and sent by a
human from their own email account; there is no email-sending
infrastructure in this repo and this tool doesn't add one.

Usage:
    ./.venv/bin/python tools/backlink_outreach_draft.py <candidate_id>
"""

import argparse
import sys
from pathlib import Path

from pydantic import BaseModel

sys.path.insert(0, str(Path(__file__).parent.parent))

from clients import db_client
from clients.deepseek_client import DeepSeekClient

SYSTEM_PROMPT = """You are drafting a short, genuine-sounding backlink outreach email from one \
website owner to another. The recipient's site already links to other pages covering this exact \
topic - that's a real, specific reason they might be interested, not a cold pitch out of nowhere.

Write a SHORT email (under 120 words): a specific opening line referencing why THIS domain is a \
good fit (it already links to similar content), one sentence naming our own relevant published \
article as the thing worth linking to, and a low-pressure close. No generic SEO-outreach filler \
("I came across your amazing website", "I think your readers would love..."), no exclamation \
points, no fake flattery. Sound like a real person who did their homework, not a template.

Return JSON matching the required schema only - subject and body (body is plain text, no HTML, \
no signature/sign-off placeholder)."""


class OutreachDraft(BaseModel):
    subject: str
    body: str


def draft_outreach(candidate_id: int) -> dict:
    candidate = db_client.get_backlink_candidate(candidate_id)
    if candidate is None:
        raise ValueError(f"No backlink_candidates row with candidate_id={candidate_id}")

    website_id = candidate["website_id"]
    published = db_client.get_published_articles(website_id, limit=5)
    if not published:
        raise ValueError(f"No published articles for website_id={website_id} - nothing to pitch")

    our_article = published[0]
    sample = (candidate.get("sample_links") or [{}])[0]

    user_prompt = (
        f"Our article to pitch: \"{our_article['title']}\" - {our_article['wp_post_url']}\n\n"
        f"Target domain: {candidate['referring_domain']}\n"
        f"They already link to: {sample.get('from_page_title') or sample.get('from_page')} "
        f"(from {sample.get('from_page')})\n"
        f"That page links out to: {sample.get('linked_to_competitor')}\n"
    )

    deepseek = DeepSeekClient()
    result = deepseek.structured_call(SYSTEM_PROMPT, user_prompt, OutreachDraft, label="backlink_outreach_draft")
    draft = result.model_dump()
    draft_text = f"Subject: {draft['subject']}\n\n{draft['body']}"

    db_client.record_backlink_outreach_draft(candidate_id, draft_text)
    return {"candidate_id": candidate_id, "draft": draft_text}


def main():
    parser = argparse.ArgumentParser(description="Draft an outreach email for one backlink candidate.")
    parser.add_argument("candidate_id", type=int)
    args = parser.parse_args()

    result = draft_outreach(args.candidate_id)
    print(result["draft"])


if __name__ == "__main__":
    main()
