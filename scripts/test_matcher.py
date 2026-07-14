import asyncio
import os
import sys
from datetime import UTC, datetime
from pathlib import Path

# Add project root and backend directory to path so we can import modules
sys.path.insert(0, str(Path(__file__).parent.parent))
sys.path.insert(0, str(Path(__file__).parent.parent / "backend"))

from plugins.builtin.codex_x_monitor.schemas import CodexXMonitorConfig, XPost
from plugins.builtin.codex_x_monitor.matcher import match_post

tweets = [
    # 1. 包含 ChatGPT Work
    "Thank you to the 7M active users who are now using Codex and ChatGPT Work.\n\nWe have added a banked reset to everyone's account to celebrate the milestone. You can apply the reset in the desktop app or on web and it will replenish the weekly usage for you.\n\nHave fun out there.",
    # 2. 包含 ChatGPT Work
    "Added a banked reset to 500k users of ChatGPT Work and Codex.",
    # 3. 之前由于未包含 Codex 导致未命中，现在应该命中（因为含有 ChatGPT Work / reset）
    "What’s been happening:\n- Just released the ability to use a banked reset from web and mobile. Before today you could only do it in the desktop app\n- Had an issue where less than 10% of users who used a banked reset and it didn’t actually reset. This was during a 2 hour window.\n- It was hard to find all the exact users where it didn’t apply, so instead granted a banked reset to everyone who pressed the reset button in that 2 hour window. This also gives us the opportunity to validate the new infra ahead of tomorrow",
    # 4
    "Tomorrow we will celebrate our 7M active users milestone and grant the first banked reset across all of our ChatGPT Work and Codex users.\n\nWe will also release a bunch of updates to the desktop app, addressing a ton of your feedback.\n\nTalk soon",
    # 5
    "Morning. The last 48 hours of Codex and ChatGPT Work have been intense! Three important updates:\n\n- Temporarily removing the 5 hour usage limit restriction for all Plus, Business and Pro plans\n- Rolling out changes that will make GPT 5.6 Sol more efficient across the board and that will be reflected in less usage being used so that it can take you further. Exact impact to be quantified and shared\n- We hit 6M active users, and are landing a usage reset in the next hour\n\nGo do things",
    # 6
    "Introducing... another usage limit reset for all our ChatGPT Work and Codex users. Should land over next 30 minutes. Hope you have an awesome weekend.\n\nThank you for pushing our systems to the absolute limit, we have never seen traffic increase so quickly. Keep the feedback coming and we'll keep shipping.",
    # 7
    "Hello beautiful people! We have reset usage limits across Codex and ChatGPT Work. And another one will come later in the day. Rejoice.\n\nNow that I have your attention, a quick update on ChatGPT Work, Codex and all the updates we shared yesterday.\n\nWe’ve spent the last 24 hours",
    # 8
    "To celebrate the launch of GPT-5.6 Sol, we will reset the rate limits again (twice) across ChatGPT Work and Codex over the next 24 hours.\n\nWe want you to have the time to truly try ambitious tasks and get the hang of it. Happy exploring!",
    # 9. 真实推文样本 1 (7月10日)
    "Enjoy a full reset of your usage limits for ChatGPT Work and Codex. Propagating in the next hour.Rolling out to Pro plans first and then all paid plans over the next 24 hours.",
    # 10. 真实推文样本 2 (6月30日)
    "Codex usage limits will be fully reset again in the next hour and we will credit one additional reset into your bank for your own usage over the next 24 hours.",
    # 11. 真实推文样本 3 (6月29日)
    "As we are still investigating, I have reset everyone's Codex usage limits. This is a hard reset given some users had stacked up to three banked resets already that they can apply on their own schedule.",
    # 12. 真实推文样本 4 (6月27日)
    "We are giving all Codex users a usage reset on the house. Should be showing in your accounts in the next few hours."
]

config = CodexXMonitorConfig(source="twscrape")

print("--- Running Codex X Monitor Matching Test (With Updated Context Rules) ---")
for idx, t in enumerate(tweets, 1):
    post = XPost(
        id=str(idx),
        author_username="test_user",
        author_display_name="Test User",
        text=t,
        url=f"https://x.com/test/status/{idx}",
        published_at=datetime.now(UTC),
        is_repost=False,
        is_reply=False
    )
    res = match_post(post, config)
    print(f"Tweet [{idx}]: {'MATCHED' if res.matched else 'NOT MATCHED'}")
    if res.matched:
        print(f"    Matched Rules: {res.matched_rules}")
    if res.excluded_by:
        print(f"    Excluded By: {res.excluded_by}")
    print("-" * 50)
