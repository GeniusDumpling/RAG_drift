"""Conservative, versioned rules. Ambiguous content is archived for review, not indexed."""

from __future__ import annotations

import html
import json
import re
from html.parser import HTMLParser
from pathlib import Path

DEFAULT_POLICY = Path(__file__).with_name("discourse_policy.json")
TECHNICAL = re.compile(
    r"\b(?:ardupilot|arducopter|arduplane|px4|pixhawk|mavlink|mavsdk|mavros|"
    r"qgroundcontrol|mission\s*planner|ekf\w*|gps|gnss|rtk|vtol|sitl|hil|"
    r"gazebo|ros\s*2?|esc|imu|i[²2]c|pid|failsafe|firmware|autopilot|"
    r"flight\s*(?:controller|log|test)|offboard|dronecan|compass|telemetry)\b|"
    r"飞控|无人机|惯导|固件|飞行日志|导航|调参",
    re.I,
)
PROMOTION = re.compile(
    r"\b(?:for sale|stock availability|hiring|job vacancy|contract work|"
    r"(?:custom|professional).{0,60}development services)\b|招聘|求职|二手出售",
    re.I,
)
COURTESY = re.compile(
    r"^(?:thanks?(?: you)?(?: very much| a lot)?|thank you|great|awesome|"
    r"following|bump|any updates?|谢谢|感谢|顶)[\s!.,。！?？]*$",
    re.I,
)
VERIFICATION = re.compile(
    r"\b(?:solved|fixed|resolved|works?(?: now)?|confirmed|reproduced)\b|已解决|解决了|验证", re.I
)


class PlainText(HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts = []
        self.skip = 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style"):
            self.skip += 1
        if tag in ("br", "p", "div", "li", "pre"):
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in ("script", "style"):
            self.skip = max(0, self.skip - 1)
        if tag in ("p", "div", "li", "pre"):
            self.parts.append("\n")

    def handle_data(self, data):
        if not self.skip:
            self.parts.append(data)


def post_text(post):
    raw = post.get("raw")
    if isinstance(raw, str):
        return raw.strip()
    parser = PlainText()
    parser.feed(str(post.get("cooked") or ""))
    return html.unescape("".join(parser.parts)).strip()


def load_policy(path=None):
    data = json.loads(Path(path or DEFAULT_POLICY).read_text())
    if not isinstance(data.get("forums"), dict) or not data.get("version"):
        raise ValueError("Policy requires version and forums")
    return data


def classify_topic(forum, topic, posts, category_path, policy):
    rules = policy["forums"].get(forum, {})
    ids = {c["id"] for c in category_path}
    slugs = {c.get("slug") for c in category_path}
    result = {"policy_version": policy["version"]}
    if ids.intersection(rules.get("exclude_category_ids", [])) or slugs.intersection(
        rules.get("exclude_category_slugs", [])
    ):
        return {**result, "decision": "reject", "reason": "excluded_category"}
    if PROMOTION.search(str(topic.get("title", ""))):
        return {**result, "decision": "reject", "reason": "promotion_or_recruitment"}
    if ids.intersection(rules.get("technical_category_ids", [])) or slugs.intersection(
        rules.get("technical_category_slugs", [])
    ):
        return {**result, "decision": "keep", "reason": "technical_category"}
    sample = str(topic.get("title", "")) + "\n" + "\n".join(post_text(p)[:4000] for p in posts[:5])
    if TECHNICAL.search(sample):
        return {**result, "decision": "keep", "reason": "technical_evidence"}
    return {**result, "decision": "review", "reason": "insufficient_domain_evidence"}


def classify_post(post):
    body = post_text(post)
    if post.get("hidden") or post.get("deleted_at"):
        return {"indexable": False, "role": "unavailable"}
    if int(post.get("post_type") or 1) != 1:
        return {"indexable": False, "role": "system_action"}
    if post.get("accepted_answer"):
        return {"indexable": True, "role": "accepted_answer"}
    without_quotes = re.sub(r"\[quote[^]]*\].*?\[/quote\]", "", body, flags=re.S | re.I)
    without_quotes = re.sub(r"^>.*$", "", without_quotes, flags=re.M).strip()
    if not without_quotes or COURTESY.fullmatch(without_quotes):
        return {"indexable": False, "role": "courtesy_or_quote"}
    text_without_media = re.sub(r"!\[[^]]*\]\([^)]*\)", "", without_quotes).strip()
    if not text_without_media:
        return {"indexable": False, "role": "attachment_only"}
    if int(post.get("post_number") or 1) == 1:
        return {"indexable": True, "role": "question_or_report"}
    if VERIFICATION.search(without_quotes):
        return {"indexable": True, "role": "verification"}
    return {"indexable": True, "role": "discussion"}
