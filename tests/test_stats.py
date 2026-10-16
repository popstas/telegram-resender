import json

import src.stats as stats_module


def test_stats_increment_and_flush(tmp_path):
    path = tmp_path / "stats.json"
    tracker = stats_module.StatsTracker(str(path), flush_interval=0)
    tracker.increment("a", forwarded=True, used_word=True)
    tracker.increment("a", forwarded=True, used_prompt=True)
    tracker.increment("b")
    tracker.add_tokens("a", 4, 6, total_tokens=10)
    tracker.add_tokens("b", 2, 3, total_tokens=5)
    tracker.flush()
    data = json.loads(path.read_text())
    assert data["stats"]["total"] == 3
    assert data["stats"]["tokens"] == 15
    assert data["stats"]["input_tokens"] == 6
    assert data["stats"]["output_tokens"] == 9
    assert data["stats"]["forwarded_total"] == 2
    assert data["stats"]["forwarded_words"] == 1
    assert data["stats"]["forwarded_prompt"] == 1
    inst_a = next(i for i in data["instances"] if i["name"] == "a")
    inst_b = next(i for i in data["instances"] if i["name"] == "b")
    assert inst_a["stats"]["total"] == 2
    assert inst_b["stats"]["total"] == 1
    assert inst_a["stats"]["tokens"] == 10
    assert inst_b["stats"]["tokens"] == 5
    assert inst_a["stats"]["input_tokens"] == 4
    assert inst_a["stats"]["output_tokens"] == 6
    assert inst_b["stats"]["input_tokens"] == 2
    assert inst_b["stats"]["output_tokens"] == 3
    assert inst_a["input_tokens"] == 4
    assert inst_a["output_tokens"] == 6
    assert inst_b["input_tokens"] == 2
    assert inst_b["output_tokens"] == 3
    assert inst_a["stats"]["forwarded_total"] == 2
    assert inst_a["stats"]["forwarded_words"] == 1
    assert inst_a["stats"]["forwarded_prompt"] == 1
    day = list(inst_a["days"].keys())[0]
    assert inst_a["days"][day]["stats"]["total"] == 2
    assert inst_a["days"][day]["stats"]["forwarded_total"] == 2
    assert inst_a["days"][day]["stats"]["forwarded_words"] == 1
    assert inst_a["days"][day]["stats"]["forwarded_prompt"] == 1
    assert inst_a["days"][day]["stats"]["tokens"] == 10
    assert inst_a["days"][day]["stats"]["input_tokens"] == 4
    assert inst_a["days"][day]["stats"]["output_tokens"] == 6


def test_convert_old_format():
    old = {
        "total": 1,
        "tokens": 2,
        "instances": [
            {"name": "a", "total": 1, "tokens": 2, "days": {"2024-01-01": 1}}
        ],
    }
    new = stats_module.convert(old)
    assert new["stats"]["total"] == 1
    assert new["stats"].get("input_tokens", 0) == 0
    assert new["stats"].get("output_tokens", 0) == 0
    inst = new["instances"][0]
    assert inst["name"] == "a"
    assert inst["stats"]["total"] == 1
    assert inst["stats"]["tokens"] == 2
    assert inst["stats"].get("input_tokens", 0) == 0
    assert inst["stats"].get("output_tokens", 0) == 0
    assert inst["days"]["2024-01-01"]["stats"]["total"] == 1


def test_folder_chats_set_and_clear(tmp_path):
    path = tmp_path / "stats.json"
    tracker = stats_module.StatsTracker(str(path), flush_interval=0)
    tracker.set_folder_chats("a", [-200, -100])
    tracker.flush()
    data = json.loads(path.read_text())
    inst_a = next(i for i in data["instances"] if i["name"] == "a")
    assert inst_a["chats"] == [-200, -100]

    tracker.clear_folder_chats("a")
    tracker.flush()
    data = json.loads(path.read_text())
    inst_a = next(i for i in data["instances"] if i["name"] == "a")
    assert "chats" not in inst_a


def test_clear_folder_chats_unknown_instance(tmp_path):
    path = tmp_path / "stats.json"
    tracker = stats_module.StatsTracker(str(path), flush_interval=0)
    tracker.clear_folder_chats("nonexistent")
    assert tracker.data["instances"] == []


def test_stats_forwarded_category(tmp_path):
    tracker = stats_module.StatsTracker(str(tmp_path / "s.json"), flush_interval=0)
    tracker.increment("a", forwarded=True, used_category=True)
    tracker.increment("a", forwarded=True, used_word=True)
    tracker.increment("a", forwarded=False, used_category=True)
    assert tracker.data["stats"]["forwarded_category"] == 1
    inst = tracker.data["instances"][0]
    assert inst["stats"]["forwarded_category"] == 1
    day = list(inst["days"].keys())[0]
    assert inst["days"][day]["stats"]["forwarded_category"] == 1
