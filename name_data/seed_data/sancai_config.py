"""三才配置表 — 125种天人地三才组合的吉凶评估。

天格/人格/地格的五行由各自笔画个位数确定:
  个位1,2=木  个位3,4=火  个位5,6=土  个位7,8=金  个位9,0=水

三才生克规则:
  天人地 相生为吉，相克为凶
  以人格为中心: 天生人=吉, 人克天=凶; 人克地=凶, 地生人=吉
"""

# 五行: 0=木 1=火 2=土 3=金 4=水
_ELEMENTS = ["木", "火", "土", "金", "水"]

# 生克: generates[i] = 被i生的元素; overcomes[i] = 被i克的元素
# (i+1)%5 = 被生, (i+2)%5 = 被克
# 实际上: 木生火(0→1), 火生土(1→2), 土生金(2→3), 金生水(3→4), 水生木(4→0)
#         木克土(0→2), 土克水(2→4), 水克火(4→1), 火克金(1→3), 金克木(3→0)


def _judge_sancai(heaven: int, man: int, earth: int) -> tuple[str, int, str]:
    """判断三才配置吉凶。heaven/man/earth 是五行索引0-4。"""
    # 人格为中心看天格和地格的关系
    score = 75  # 基准分

    # 天格对人格
    heaven_generates_man = (heaven + 1) % 5 == man or (heaven + 3) % 5 == man  # wait let me check

    # Let me use a clearer approach
    # generates[i] = (i+1)%5  means i生(i+1)%5
    # overcomes[i] = (i+2)%5 means i克(i+2)%5

    details = []

    # 天 → 人
    if (heaven + 1) % 5 == man:
        details.append("天格生人格(吉)")
        score += 10
    elif (heaven + 2) % 5 == man:
        details.append("天格克人格(凶)")
        score -= 15
    elif heaven == man:
        details.append("天格人格比和(平)")
        score += 5
    elif (man + 1) % 5 == heaven:
        details.append("人格生天格(平)")
        score -= 5
    elif (man + 2) % 5 == heaven:
        details.append("人格克天格(平)")
        score += 0

    # 人 → 地
    if (man + 1) % 5 == earth:
        details.append("人格生地格(平)")
        score -= 5
    elif (man + 2) % 5 == earth:
        details.append("人格克地格(半吉)")
        score += 5
    elif man == earth:
        details.append("人格地格比和(平)")
        score += 5
    elif (earth + 1) % 5 == man:
        details.append("地格生人格(吉)")
        score += 10
    elif (earth + 2) % 5 == man:
        details.append("地格克人格(凶)")
        score -= 15

    # 天 → 地
    if (heaven + 1) % 5 == earth:
        details.append("天格生地格(平)")
        score += 3
    elif (heaven + 2) % 5 == earth:
        details.append("天格克地格(平)")
        score -= 3

    details_text = "；".join(details)

    # 确定吉凶等级
    if score >= 90:
        ji_xiong = "大吉"
    elif score >= 75:
        ji_xiong = "吉"
    elif score >= 60:
        ji_xiong = "中吉"
    elif score >= 45:
        ji_xiong = "平"
    elif score >= 30:
        ji_xiong = "凶"
    else:
        ji_xiong = "大凶"

    return ji_xiong, score, details_text


def generate_sancai_config():
    """生成全部125种三才配置。"""
    configs = []
    idx = 1
    for h in range(5):      # 天格五行
        for m in range(5):  # 人格五行
            for e in range(5):  # 地格五行
                ji_xiong, score, desc = _judge_sancai(h, m, e)
                configs.append((
                    idx,
                    _ELEMENTS[h],
                    _ELEMENTS[m],
                    _ELEMENTS[e],
                    ji_xiong,
                    f"天{_ELEMENTS[h]}人{_ELEMENTS[m]}地{_ELEMENTS[e]}: {desc}",
                    score,
                ))
                idx += 1
    return configs


SANCAI_CONFIG = generate_sancai_config()
