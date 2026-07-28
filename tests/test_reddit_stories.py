"""Gate tests for reddit_stories.py -- fixtures only, no network, no Claude."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import reddit_stories as rs


def post(**over):
    base = {
        "id": "abc123", "title": "AITA for leaving the wedding early?",
        "selftext": "word " * 500, "score": 5000,
        "stickied": False, "over_18": False, "subreddit": "AITAH",
    }
    base.update(over)
    return base


# ---- selection bar ---------------------------------------------------------

def test_eligible_happy_path():
    assert rs.eligible(post(), used=set())


def test_eligible_rejects_each_bar():
    assert not rs.eligible(post(stickied=True), set())
    assert not rs.eligible(post(over_18=True), set())
    assert not rs.eligible(post(selftext="too short"), set())
    assert not rs.eligible(post(selftext="word " * 2000), set())
    assert not rs.eligible(post(score=200), set())
    assert not rs.eligible(post(), {"abc123"})                # already made
    assert not rs.eligible(post(title="story about abuse"), set())


def test_eligible_min_score_override():
    assert rs.eligible(post(score=600), set(), min_score=500)


DISTINCT_WORDS = ["wedding", "fridge", "parking", "laundry", "garden",
                  "karaoke", "birthday", "camping"]
SECOND_WORDS = ["alpha", "bravo", "charlie", "delta", "echoes",
                "foxtrot", "golfer", "hotels"]


def distinct_post(i, **over):
    w, w2 = DISTINCT_WORDS[i], SECOND_WORDS[i]
    return post(id=f"p{i}", title=f"AITA regarding the {w} {w2} fiasco?",
                selftext=f"a {w} story with distinctly {w2} words each time " * 60,
                **over)


def test_select_sorts_by_score_and_caps():
    posts = [distinct_post(i, score=1000 + i * 100) for i in range(6)]
    picked = rs.select_stories(posts, set(), count=3)
    assert [p["id"] for p in picked] == ["p5", "p4", "p3"]


def test_select_skips_used():
    posts = [post(id="a"), post(id="b", score=9000)]
    picked = rs.select_stories(posts, {"b"}, count=5)
    assert [p["id"] for p in picked] == ["a"]


# ---- repost / dupe detection -----------------------------------------------

STORY = ("My sister asked me to put in a good word for her at my company "
         "even though she was fired from her last three jobs for showing up "
         "late every single day and I told her no because my reputation "
         "matters more than her feelings and now the whole family says I am "
         "selfish and should apologize at dinner on Sunday ") * 12

REWORDED = STORY.replace("sister", "younger sister").replace(
    "selfish", "heartless").replace("Sunday", "Saturday")


def test_dupe_catches_reworded_repost():
    made = post(id="old1", selftext=STORY)
    entries = {"old1": rs.signature(made)}
    repost = post(id="new9", title="AITA for not helping my sister get a job?",
                  selftext=REWORDED)
    match = rs.dupe_of(repost, entries)
    assert match is not None and match[0] == "old1"


def test_dupe_passes_unrelated_story():
    entries = {"old1": rs.signature(post(id="old1", selftext=STORY))}
    fresh = post(id="new2", title="AITA for eating my roommate's leftovers?",
                 selftext="Completely different words about pizza in the "
                          "fridge and a passive aggressive note " * 30)
    assert rs.dupe_of(fresh, entries) is None


def test_select_rejects_reposts_and_same_run_twins():
    entries = {"old1": rs.signature(post(id="old1", selftext=STORY))}
    twin_a = post(id="tw1", title="AITA about my roommate and the fridge?",
                  selftext="One brand new story told in many "
                           "different original words " * 40)
    twin_b = post(id="tw2", title="Same fridge drama posted again an hour later",
                  selftext=twin_a["selftext"])
    repost = post(id="rp1", title="Family thinks I owe my sibling an apology",
                  selftext=REWORDED)
    picked = rs.select_stories([twin_a, twin_b, repost], entries, count=5)
    assert [p["id"] for p in picked] == ["tw1"]  # repost + twin both rejected


def test_history_migrates_v1_id_list(tmp_path):
    old = tmp_path / ".used.json"
    old.write_text('["aaa", "bbb"]', encoding="utf-8")
    hist = rs.load_history(old)
    assert set(hist) == {"aaa", "bbb"}
    assert rs.load_used(old) == {"aaa", "bbb"}       # id blocking still works
    hist["ccc"] = rs.signature(post(id="ccc"))
    rs.save_history(hist, old)
    again = rs.load_history(old)
    assert set(again) == {"aaa", "bbb", "ccc"}
    assert again["ccc"]["t"]                          # signatures persist


# ---- naming, state, prompt, validation -------------------------------------

def test_story_slug():
    slug = rs.story_slug(post())
    assert slug.startswith("abc123-aita-for-leaving-the-wedding")
    assert len(slug) <= 40


def test_used_state_roundtrip(tmp_path):
    state = tmp_path / ".used.json"
    rs.save_used({"b", "a"}, state)
    assert rs.load_used(state) == {"a", "b"}
    assert rs.load_used(tmp_path / "missing.json") == set()


def test_retell_prompt_contains_story_rules_and_sentinels():
    p = rs.build_retell_prompt("The title", "The body text")
    assert "The title" in p and "The body text" in p
    assert rs.SENTINEL_BEGIN in p and rs.SENTINEL_END in p
    assert "anonymize" in p and "PG-13" in p
    assert "whose side" in p


def test_retell_prompt_truncates_monster_posts():
    p = rs.build_retell_prompt("t", "x" * 10000)
    assert len(p) < 5000


def test_valid_retell():
    good = "word " * 150
    assert rs.valid_retell(good)
    assert not rs.valid_retell(None)
    assert not rs.valid_retell("too short")
    assert not rs.valid_retell("word " * 400)
    assert not rs.valid_retell(("word " * 150) + " see http://x.com")
    assert not rs.valid_retell(("word " * 150) + " over on reddit")


RSS_FIXTURE = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <entry>
    <id>t3_zz9</id>
    <title>AITA for eating the &amp;quot;shared&amp;quot; cake?</title>
    <author><name>/u/storyteller99</name></author>
    <content type="html">&lt;div class="md"&gt;&lt;p&gt;{body}&lt;/p&gt;&lt;/div&gt;</content>
  </entry>
  <entry>
    <id>t3_empty</id>
    <title>no content entry</title>
  </entry>
</feed>""".replace("{body}", "word " * 500)


def test_parse_rss_builds_post_dicts():
    posts = rs.parse_rss(RSS_FIXTURE, "AITAH")
    assert len(posts) == 1                       # content-less entry dropped
    p = posts[0]
    assert p["id"] == "zz9" and p["subreddit"] == "AITAH"
    assert p["score"] is None
    assert p["author"] == "storyteller99"        # /u/ prefix stripped
    assert "<" not in p["selftext"]              # html stripped
    assert 490 <= len(p["selftext"].split()) <= 510


def test_fetch_with_retry_cools_off_on_429(monkeypatch):
    calls = []

    def flaky(sub, t):
        calls.append(sub)
        if len(calls) == 1:
            raise RuntimeError("HTTP Error 429: Too Many Requests")
        return [{"id": "ok"}]

    monkeypatch.setattr(rs, "fetch_top", flaky)
    monkeypatch.setattr(rs.time, "sleep", lambda s: None)
    assert rs.fetch_with_retry("AITAH", "day") == [{"id": "ok"}]
    assert len(calls) == 2


def test_eligible_accepts_rss_posts_without_scores():
    assert rs.eligible(post(score=None), set())


def test_select_preserves_feed_order_for_unscored():
    posts = [distinct_post(i, score=None) for i in range(4)]
    for i, p in enumerate(posts):
        p["id"] = f"r{i}"
    picked = rs.select_stories(posts, set(), count=3)
    assert [p["id"] for p in picked] == ["r0", "r1", "r2"]  # feed order kept


def test_fetch_prefers_rss_single_request(monkeypatch):
    """RSS is the endpoint that works; it must be the ONLY request made when
    it succeeds (extra requests were tripping Reddit's 429s)."""
    seen = []
    monkeypatch.setattr(rs, "_get",
                        lambda url, agent: seen.append(url) or RSS_FIXTURE.encode())
    posts = rs.fetch_top("AITAH")
    assert len(seen) == 1 and "top.rss" in seen[0]
    assert posts and posts[0]["id"] == "zz9"


def test_fetch_falls_back_to_json_when_rss_dies(monkeypatch):
    import json as _json
    seen = []

    def fake_get(url, agent):
        seen.append(url)
        if "top.rss" in url:
            raise RuntimeError("rss gone")
        return _json.dumps({"data": {"children": [{"data": {"id": "x1"}}]}}).encode()

    monkeypatch.setattr(rs, "_get", fake_get)
    posts = rs.fetch_top("AITAH")
    assert posts == [{"id": "x1"}]
    assert "top.rss" in seen[0] and "www.reddit.com" in seen[1]


def test_retell_retries_then_gives_up(monkeypatch):
    calls = []
    monkeypatch.setattr(rs, "ask_claude",
                        lambda p, **k: calls.append(p) or "no sentinels here")
    assert rs.retell(post()) is None
    assert len(calls) == 2
    assert "REMINDER" in calls[1]
