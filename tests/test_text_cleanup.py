import pytest

from text_cleanup import RuleCleaner, TextCleaner


@pytest.mark.parametrize("raw, expected", [
    ("呃，今天先测试。", "今天先测试。"),
    ("嗯，呃，我们先测试 Qwen3-ASR。", "我们先测试 Qwen3-ASR。"),
    ("今天，嗯，先测试。", "今天，先测试。"),
    ("今天，嗯。明天继续。", "今天。明天继续。"),
    ("测试，呃；继续。", "测试；继续。"),
    ("完成了。呃，接着测试。", "完成了。接着测试。"),
    ("嗯嗯，先测试。", "先测试。"),
    ("嗯，第一项。\n\n呃，第二项。", "第一项。\n\n第二项。"),
    ("我就是不同意，然后处理那个文件啊。", "我就是不同意，然后处理那个文件啊。"),
    ("嗯我们今天测试。", "嗯我们今天测试。"),
    ("嗯。", "嗯。"),
    ("嗯？", "嗯？"),
    ("嗯？你说什么？", "嗯？你说什么？"),
    ("不要删掉“呃”这个字。", "不要删掉“呃”这个字。"),
    ('他说"嗯，测试"。', '他说"嗯，测试"。'),
    ("`嗯，` 和 ```python\n呃，\n``` 不变。", "`嗯，` 和 ```python\n呃，\n``` 不变。"),
    ("呃，调用 https://example.com/嗯 和 /tmp/呃.txt。",
     "调用 https://example.com/嗯 和 /tmp/呃.txt。"),
    ("邮箱 um@example.com、版本 v3.14、价格 300 元、5 mm 不要改。",
     "邮箱 um@example.com、版本 v3.14、价格 300 元、5 mm 不要改。"),
    ("我们我们先测试，不不不，不要删除。", "我们我们先测试，不不不，不要删除。"),
    ("  原有空白\n\n和  缩进  不变。 ", "  原有空白\n\n和  缩进  不变。 "),
    ("", ""),
])
def test_conservative_rules(raw, expected):
    assert RuleCleaner().clean(raw) == expected


def test_configured_fillers_are_escaped_and_still_protected():
    cleaner = RuleCleaner(["uh+"])
    assert cleaner.clean('uh+，保留 "uh+"。') == '保留 "uh+"。'


def test_empty_filler_list_disables_removal():
    assert RuleCleaner([]).clean("呃，测试。") == "呃，测试。"


def test_missing_config_is_exact_passthrough():
    assert TextCleaner().clean("呃，测试。").text == "呃，测试。"


def test_rules_mode_does_not_construct_smart_client():
    result = TextCleaner({"enabled": True, "mode": "rules"}).clean("呃，测试。")
    assert result.text == "测试。"
    assert result.mode == "rules"
    assert result.fallback_reason is None


def test_disabled_wins_over_mode():
    assert TextCleaner({"enabled": False, "mode": "minimax"}).clean("嗯，测试。").text == "嗯，测试。"


def test_explicit_empty_fillers_is_respected():
    assert TextCleaner({"enabled": True, "fillers": []}).clean("呃，测试。").text == "呃，测试。"


@pytest.mark.parametrize("raw, expected", [
    # ASR 前导标点噪声（瞬态+停顿导致标点模型开空语段）
    ("，好的，我想问一下。", "好的，我想问一下。"),
    ("， ， 好的", " ， 好的"),                  # 空格不剥（缩进保护），仅剥标点
    ("。开始。", "开始。"),
    ("；：！？～开始", "开始"),
    ("，呃，今天测试。", "今天测试。"),           # 前导逗号 + 犹豫词
    # 不该剥的场景
    (".gitignore 要提交吗", ".gitignore 要提交吗"),  # ASCII 句点保护
    ("「你好」他说的。", "「你好」他说的。"),        # 开引号保护
    ("“引用开头。”", "“引用开头。”"),
    ("`code` 开头", "`code` 开头"),
    ("3.14 是圆周率。", "3.14 是圆周率。"),
    ("你好。", "你好。"),
])
def test_leading_separator_strip(raw, expected):
    assert RuleCleaner().clean(raw) == expected


def test_leading_strip_with_empty_filler_list():
    cleaner = RuleCleaner([])
    assert cleaner.clean("，你好") == "你好"


def test_leading_strip_punctuation_only_text_unchanged():
    assert RuleCleaner().clean("，，，") == "，，，"


def test_off_mode_never_strips():
    result = TextCleaner({"enabled": False}).clean("，你好。")
    assert result.text == "，你好。"
    assert result.mode == "off"


def test_minimax_output_also_strips_leading_separator():
    class StubSmart:
        def clean(self, text):
            return {"text": "，智能清洗结果。"}
        def close(self):
            pass
    result = TextCleaner({"enabled": True, "mode": "minimax"},
                         smart_client=StubSmart()).clean("，智能清洗结果。")
    assert result.text == "智能清洗结果。"
    assert result.mode == "minimax"
