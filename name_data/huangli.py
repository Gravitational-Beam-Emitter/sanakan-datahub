"""
Chinese Almanac (黄历) — based on 《协纪辨方书》(Qianlong reign, Qing Dynasty).

Systems:
  1. 建除十二神 (Jianchu Twelve Gods) — day designation by month/day branch
  2. 黄道黑道十二神 (Yellow/Black Path Officers) — auspicious/inauspicious day officers
  3. 二十八宿 (28 Lunar Mansions) — daily mansion governing the day
  4. 彭祖百忌 (Peng Zu's Hundred Taboos) — stem/branch taboos
  5. 宜忌综合推算 (Activity Suitability) — combined yi/ji computation

Reference:
  允禄, 何国宗 等.《协纪辨方书》. 清乾隆六年(1741)武英殿刻本.

Usage:
  from name_data.huangli import compute_daily_almanac, get_daily_almanac
"""

from __future__ import annotations

import json
from datetime import date as dt_date
from typing import Optional

from name_data.calendar import (
    HEAVENLY_STEMS, EARTHLY_BRANCHES,
    day_ganzhi_index, month_ganzhi, gregorian_to_ganzhi,
)
from name_data.storage import init_db

# ══════════════════════════════════════════════════════════════════════
# 1. 建除十二神 (Jianchu Twelve Gods)
#    Formula: jianchu_index = (day_zhi_idx - month_zhi_idx + 12) % 12
# ══════════════════════════════════════════════════════════════════════

JIANCHU_GODS = [
    {"name": "建", "index": 0,  "description": "建日：万物开始生长之日，宜祭祀祈福，忌动土开仓"},
    {"name": "除", "index": 1,  "description": "除日：除旧布新之日，宜解除沐浴扫舍，忌嫁娶入宅"},
    {"name": "满", "index": 2,  "description": "满日：圆满充盈之日，宜祭祀祈福，忌服药出行"},
    {"name": "平", "index": 3,  "description": "平日：平稳平常之日，宜修饰垣墙，忌开渠穿井"},
    {"name": "定", "index": 4,  "description": "定日：安定稳定之日，宜订婚嫁娶移徙，忌词讼"},
    {"name": "执", "index": 5,  "description": "执日：执守固守之日，宜捕捉畋猎，忌开市出行"},
    {"name": "破", "index": 6,  "description": "破日：破败冲破之日，宜破屋坏垣，余事不宜"},
    {"name": "危", "index": 7,  "description": "危日：危险谨慎之日，宜祭祀安床，忌出行移徙"},
    {"name": "成", "index": 8,  "description": "成日：成就成功之日，宜嫁娶开市入宅出行，忌词讼"},
    {"name": "收", "index": 9,  "description": "收日：收纳入库之日，宜纳财捕捉，忌出行嫁娶"},
    {"name": "开", "index": 10, "description": "开日：开放通达之日，宜嫁娶开市出行入宅，忌安葬"},
    {"name": "闭", "index": 11, "description": "闭日：闭塞收敛之日，宜祭祀补垣塞穴，忌出行开市"},
]

JIANCHU_YIJI = {
    "建": {"yi": ["祭祀", "祈福", "求嗣", "入学"], "ji": ["动土", "开仓", "掘井", "安葬"]},
    "除": {"yi": ["解除", "沐浴", "扫舍", "求医", "疗病"], "ji": ["嫁娶", "入宅", "移徙", "开市"]},
    "满": {"yi": ["祭祀", "祈福", "求嗣"], "ji": ["服药", "求医", "出行", "移徙"]},
    "平": {"yi": ["修饰垣墙", "平治道涂", "祭祀"], "ji": ["开渠", "穿井", "开市", "立券"]},
    "定": {"yi": ["祭祀", "祈福", "订盟", "纳采", "嫁娶", "移徙", "入宅", "安床"], "ji": ["词讼", "出行"]},
    "执": {"yi": ["捕捉", "畋猎", "祭祀"], "ji": ["开市", "交易", "出行", "移徙", "嫁娶"]},
    "破": {"yi": ["破屋坏垣", "祭祀"], "ji": ["嫁娶", "入宅", "移徙", "开市", "出行", "安床"]},
    "危": {"yi": ["祭祀", "祈福", "安床", "拆卸"], "ji": ["出行", "移徙", "嫁娶", "开市"]},
    "成": {"yi": ["祭祀", "祈福", "嫁娶", "开市", "入宅", "出行", "移徙", "安床", "交易"], "ji": ["词讼"]},
    "收": {"yi": ["祭祀", "祈福", "纳财", "捕捉", "畋猎"], "ji": ["出行", "移徙", "嫁娶", "开市", "安床"]},
    "开": {"yi": ["祭祀", "祈福", "嫁娶", "开市", "出行", "入宅", "移徙", "交易", "立券"], "ji": ["安葬", "行丧"]},
    "闭": {"yi": ["祭祀", "祈福", "补垣塞穴", "安葬"], "ji": ["出行", "移徙", "开市", "嫁娶", "入宅"]},
}

# Score per jianchu god for overall almanac assessment
JIANCHU_SCORES = {
    "建": 10, "除": 30, "满": 20, "平": 0, "定": 25, "执": 0,
    "破": -80, "危": -20, "成": 55, "收": 5, "开": 45, "闭": -30,
}


# ══════════════════════════════════════════════════════════════════════
# 2. 黄道黑道十二神 (Yellow/Black Path Officers)
#    From 《协纪辨方书》卷五·黄道黑道
# ══════════════════════════════════════════════════════════════════════

# 青龙 starting day-branch for each month branch
# Pattern: 寅申→子, 卯酉→寅, 辰戌→辰, 巳亥→午, 午子→申, 未丑→戌
_QINGLONG_START = {
    "寅": "子", "申": "子",
    "卯": "寅", "酉": "寅",
    "辰": "辰", "戌": "辰",
    "巳": "午", "亥": "午",
    "午": "申", "子": "申",
    "未": "戌", "丑": "戌",
}

YELLOW_BLACK_OFFICERS = [
    {"name": "青龙", "type": "黄道", "ji_xiong": "吉", "description": "青龙：黄道吉神，主喜庆，宜嫁娶出行开市"},
    {"name": "明堂", "type": "黄道", "ji_xiong": "吉", "description": "明堂：黄道吉神，主贵人，宜祭祀祈福"},
    {"name": "天刑", "type": "黑道", "ji_xiong": "凶", "description": "天刑：黑道凶神，主刑伤，忌出行争讼"},
    {"name": "朱雀", "type": "黑道", "ji_xiong": "凶", "description": "朱雀：黑道凶神，主口舌，忌词讼"},
    {"name": "金匮", "type": "黄道", "ji_xiong": "吉", "description": "金匮：黄道吉神，主财帛，宜纳财交易"},
    {"name": "天德", "type": "黄道", "ji_xiong": "吉", "description": "天德：黄道吉神，主福佑，百事皆宜"},
    {"name": "白虎", "type": "黑道", "ji_xiong": "凶", "description": "白虎：黑道凶神，主血光，忌出行嫁娶"},
    {"name": "玉堂", "type": "黄道", "ji_xiong": "吉", "description": "玉堂：黄道吉神，主文昌，宜入学出行"},
    {"name": "天牢", "type": "黑道", "ji_xiong": "凶", "description": "天牢：黑道凶神，主牢狱，忌出行词讼"},
    {"name": "玄武", "type": "黑道", "ji_xiong": "凶", "description": "玄武：黑道凶神，主盗贼，忌出行"},
    {"name": "司命", "type": "黄道", "ji_xiong": "吉", "description": "司命：黄道吉神，主福寿，宜祈福祭祀"},
    {"name": "勾陈", "type": "黑道", "ji_xiong": "凶", "description": "勾陈：黑道凶神，主迟滞，忌出行立券"},
]


# ══════════════════════════════════════════════════════════════════════
# 3. 二十八宿 (28 Lunar Mansions)
#    Daily cycle: mansion_index = (REF_OFFSET + days_since_ref_date) % 28
# ══════════════════════════════════════════════════════════════════════

# Reference: 2024-01-01 is known to be 角木蛟 from Chinese almanac sources.
# A secondary reference: the traditional rule that 虚日鼠 (index 10) aligns with
# the 甲子 day that falls near the winter solstice.
_MANSION_REF_DATE = dt_date(2024, 1, 1)
_MANSION_REF_INDEX = 0  # 角木蛟 = index 0

MANSIONS_28 = [
    # ── 东方青龙七宿 ──
    {"index": 0,  "name": "角木蛟", "short": "角", "group": "青龙", "luminary": "木", "animal": "蛟",
     "yi": ["嫁娶", "祭祀", "入学", "出行", "裁衣", "移徙", "开市"],
     "ji": ["安葬", "开仓"]},
    {"index": 1,  "name": "亢金龙", "short": "亢", "group": "青龙", "luminary": "金", "animal": "龙",
     "yi": ["嫁娶", "祭祀", "出行", "栽种", "纳畜"],
     "ji": ["开渠", "穿井", "安葬"]},
    {"index": 2,  "name": "氐土貉", "short": "氐", "group": "青龙", "luminary": "土", "animal": "貉",
     "yi": ["嫁娶", "祭祀", "出行", "开市", "交易", "纳财"],
     "ji": ["安葬", "词讼"]},
    {"index": 3,  "name": "房日兔", "short": "房", "group": "青龙", "luminary": "日", "animal": "兔",
     "yi": ["嫁娶", "祭祀", "祈福", "出行", "移徙", "入宅", "开市", "交易"],
     "ji": []},
    {"index": 4,  "name": "心月狐", "short": "心", "group": "青龙", "luminary": "月", "animal": "狐",
     "yi": ["祭祀", "祈福", "嫁娶", "订盟"],
     "ji": ["出行", "移徙", "开市"]},
    {"index": 5,  "name": "尾火虎", "short": "尾", "group": "青龙", "luminary": "火", "animal": "虎",
     "yi": ["嫁娶", "开市", "交易", "栽种", "纳财"],
     "ji": ["穿井", "安葬", "移徙"]},
    {"index": 6,  "name": "箕水豹", "short": "箕", "group": "青龙", "luminary": "水", "animal": "豹",
     "yi": ["嫁娶", "祭祀", "出行", "修筑", "开市"],
     "ji": ["词讼"]},
    # ── 北方玄武七宿 ──
    {"index": 7,  "name": "斗木獬", "short": "斗", "group": "玄武", "luminary": "木", "animal": "獬",
     "yi": ["嫁娶", "祭祀", "出行", "开市", "安床", "裁衣"],
     "ji": ["栽种"]},
    {"index": 8,  "name": "牛金牛", "short": "牛", "group": "玄武", "luminary": "金", "animal": "牛",
     "yi": ["祭祀", "祈福", "入学"],
     "ji": ["嫁娶", "出行", "入宅", "移徙"]},
    {"index": 9,  "name": "女土蝠", "short": "女", "group": "玄武", "luminary": "土", "animal": "蝠",
     "yi": ["祭祀", "祈福", "纳采"],
     "ji": ["嫁娶", "开市", "出行", "入宅"]},
    {"index": 10, "name": "虚日鼠", "short": "虚", "group": "玄武", "luminary": "日", "animal": "鼠",
     "yi": ["祭祀", "祈福", "安葬"],
     "ji": ["嫁娶", "出行", "移徙", "开市", "入宅"]},
    {"index": 11, "name": "危月燕", "short": "危", "group": "玄武", "luminary": "月", "animal": "燕",
     "yi": ["祭祀", "祈福", "安床"],
     "ji": ["嫁娶", "出行", "移徙", "开市"]},
    {"index": 12, "name": "室火猪", "short": "室", "group": "玄武", "luminary": "火", "animal": "猪",
     "yi": ["嫁娶", "祭祀", "祈福", "出行", "入宅", "开市", "移徙", "安床"],
     "ji": []},
    {"index": 13, "name": "壁水貐", "short": "壁", "group": "玄武", "luminary": "水", "animal": "貐",
     "yi": ["嫁娶", "祭祀", "入学", "出行", "开市", "裁衣"],
     "ji": []},
    # ── 西方白虎七宿 ──
    {"index": 14, "name": "奎木狼", "short": "奎", "group": "白虎", "luminary": "木", "animal": "狼",
     "yi": ["嫁娶", "祭祀", "出行", "开市", "交易", "安葬", "修造"],
     "ji": ["词讼"]},
    {"index": 15, "name": "娄金狗", "short": "娄", "group": "白虎", "luminary": "金", "animal": "狗",
     "yi": ["嫁娶", "祭祀", "出行", "开市", "栽种", "纳畜"],
     "ji": ["词讼"]},
    {"index": 16, "name": "胃土雉", "short": "胃", "group": "白虎", "luminary": "土", "animal": "雉",
     "yi": ["祭祀", "祈福", "嫁娶", "纳采"],
     "ji": ["出行", "开市", "移徙"]},
    {"index": 17, "name": "昴日鸡", "short": "昴", "group": "白虎", "luminary": "日", "animal": "鸡",
     "yi": ["祭祀", "祈福"],
     "ji": ["嫁娶", "出行", "移徙", "开市", "安床"]},
    {"index": 18, "name": "毕月乌", "short": "毕", "group": "白虎", "luminary": "月", "animal": "乌",
     "yi": ["祭祀", "祈福", "嫁娶", "出行", "开市", "交易"],
     "ji": ["安葬", "词讼"]},
    {"index": 19, "name": "觜火猴", "short": "觜", "group": "白虎", "luminary": "火", "animal": "猴",
     "yi": ["祭祀", "祈福"],
     "ji": ["嫁娶", "出行", "移徙", "开市", "词讼"]},
    {"index": 20, "name": "参水猿", "short": "参", "group": "白虎", "luminary": "水", "animal": "猿",
     "yi": ["祭祀", "祈福", "嫁娶", "出行", "开市"],
     "ji": ["词讼"]},
    # ── 南方朱雀七宿 ──
    {"index": 21, "name": "井木犴", "short": "井", "group": "朱雀", "luminary": "木", "animal": "犴",
     "yi": ["祭祀", "祈福", "嫁娶", "出行", "开市", "栽种", "纳畜"],
     "ji": ["安葬"]},
    {"index": 22, "name": "鬼金羊", "short": "鬼", "group": "朱雀", "luminary": "金", "animal": "羊",
     "yi": ["祭祀", "祈福"],
     "ji": ["嫁娶", "出行", "移徙", "开市", "词讼", "入宅"]},
    {"index": 23, "name": "柳土獐", "short": "柳", "group": "朱雀", "luminary": "土", "animal": "獐",
     "yi": ["祭祀", "祈福"],
     "ji": ["嫁娶", "出行", "移徙", "开市", "安葬"]},
    {"index": 24, "name": "星日马", "short": "星", "group": "朱雀", "luminary": "日", "animal": "马",
     "yi": ["嫁娶", "祭祀", "祈福", "出行", "开市", "交易"],
     "ji": ["词讼"]},
    {"index": 25, "name": "张月鹿", "short": "张", "group": "朱雀", "luminary": "月", "animal": "鹿",
     "yi": ["嫁娶", "祭祀", "祈福", "出行", "开市", "交易"],
     "ji": ["安葬"]},
    {"index": 26, "name": "翼火蛇", "short": "翼", "group": "朱雀", "luminary": "火", "animal": "蛇",
     "yi": ["嫁娶", "祭祀", "祈福", "出行", "开市", "栽种"],
     "ji": ["安葬"]},
    {"index": 27, "name": "轸水蚓", "short": "轸", "group": "朱雀", "luminary": "水", "animal": "蚓",
     "yi": ["嫁娶", "祭祀", "祈福", "入学", "出行", "移徙"],
     "ji": ["开仓", "安葬"]},
]

# Build lookup by short name for fast access
_MANSION_BY_SHORT = {m["short"]: m for m in MANSIONS_28}


# ══════════════════════════════════════════════════════════════════════
# 4. 彭祖百忌 (Peng Zu's Hundred Taboos)
#    Ten stem taboos + twelve branch taboos
# ══════════════════════════════════════════════════════════════════════

PENGZU_STEM_TABOOS = {
    "甲": "甲不开仓财物耗散",
    "乙": "乙不栽种千株不长",
    "丙": "丙不修灶必见灾殃",
    "丁": "丁不剃头头必生疮",
    "戊": "戊不受田田主不祥",
    "己": "己不破券二比并亡",
    "庚": "庚不经络织机虚张",
    "辛": "辛不合酱主人不尝",
    "壬": "壬不决水更难提防",
    "癸": "癸不词讼理弱敌强",
}

PENGZU_BRANCH_TABOOS = {
    "子": "子不问卜自惹祸殃",
    "丑": "丑不冠带主不还乡",
    "寅": "寅不祭祀神鬼不尝",
    "卯": "卯不穿井水泉不香",
    "辰": "辰不哭泣必主重丧",
    "巳": "巳不远行财物伏藏",
    "午": "午不苫盖屋主更张",
    "未": "未不服药毒气入肠",
    "申": "申不安床鬼祟入房",
    "酉": "酉不会客醉坐颠狂",
    "戌": "戌不吃犬作怪上床",
    "亥": "亥不嫁娶不利新郎",
}


# ══════════════════════════════════════════════════════════════════════
# Core Computation Functions
# ══════════════════════════════════════════════════════════════════════

def compute_jianchu(month_zhi: str, day_zhi: str) -> dict:
    """Compute 建除十二神 designation for a given month/day earthly branch pair.

    Formula from 《协纪辨方书》卷四:
      jianchu_index = (day_zhi_index - month_zhi_index + 12) % 12

    Args:
        month_zhi: month earthly branch (e.g. "寅" for first month starting at 立春)
        day_zhi: day earthly branch (e.g. "子")

    Returns dict with god name, index, description, yi, ji, and score.
    """
    month_idx = EARTHLY_BRANCHES.index(month_zhi)
    day_idx = EARTHLY_BRANCHES.index(day_zhi)
    jc_idx = (day_idx - month_idx + 12) % 12

    god = JIANCHU_GODS[jc_idx]
    yiji = JIANCHU_YIJI[god["name"]]

    return {
        "god": god["name"],
        "index": jc_idx,
        "description": god["description"],
        "yi": yiji["yi"],
        "ji": yiji["ji"],
        "score": JIANCHU_SCORES[god["name"]],
    }


def compute_yellow_black(month_zhi: str, day_zhi: str) -> dict:
    """Compute 黄道黑道十二神 officer for a given month/day branch pair.

    Algorithm from 《协纪辨方书》卷五:
      1. Find the day branch where 青龙 starts for this month branch.
      2. Count offset from 青龙 start to actual day branch.
      3. Index into YELLOW_BLACK_OFFICERS cycle.

    Returns dict with officer name, type (黄道/黑道), ji_xiong, and description.
    """
    qinglong_zhi = _QINGLONG_START[month_zhi]
    qinglong_idx = EARTHLY_BRANCHES.index(qinglong_zhi)
    day_idx = EARTHLY_BRANCHES.index(day_zhi)

    officer_idx = (day_idx - qinglong_idx + 12) % 12
    officer = YELLOW_BLACK_OFFICERS[officer_idx]

    return {
        "officer": officer["name"],
        "type": officer["type"],
        "ji_xiong": officer["ji_xiong"],
        "description": officer["description"],
        "is_auspicious": officer["type"] == "黄道",
    }


def compute_mansion(d: dt_date) -> dict:
    """Compute the 28 Lunar Mansion (二十八宿) governing a given date.

    Uses daily cycle method: mansion_index = (REF_INDEX + days_since_ref) % 28.

    Reference: 2024-01-01 is 角木蛟 (index 0).
    """
    days_diff = (d - _MANSION_REF_DATE).days
    idx = (_MANSION_REF_INDEX + days_diff) % 28
    return MANSIONS_28[idx].copy()


def compute_pengzu_taboos(day_stem: str, day_branch: str) -> dict:
    """Get Peng Zu's Hundred Taboos (彭祖百忌) for a given day stem and branch.

    Returns both stem and branch taboos with plain-text descriptions.
    """
    return {
        "stem_taboo": PENGZU_STEM_TABOOS.get(day_stem, ""),
        "branch_taboo": PENGZU_BRANCH_TABOOS.get(day_branch, ""),
        "day_stem": day_stem,
        "day_branch": day_branch,
    }


def compute_yiji(jianchu: dict, yellow_black: dict, mansion: dict) -> dict:
    """Compute combined daily suitable (宜) and avoid (忌) activities.

    Rules (from 《协纪辨方书》择日原则):
      1. Base = jianchu yi/ji sets
      2. 黄道 modifier: boosts auspiciousness, adds no extra restrictions
      3. 黑道 modifier: adds major restrictions (忌嫁娶/入宅/开市)
      4. 二十八宿 intersection: yi = base_yi - mansion_ji; ji = base_ji | mansion_ji
      5. Special cases: 破日 gets "诸事不宜" override

    Returns dict with yi list, ji list, and combined score (-100 to 100).
    """
    yi = set(jianchu["yi"])
    ji = set(jianchu["ji"])

    # 破日 override: nothing is suitable
    if jianchu["god"] == "破":
        yi = set()
        ji.add("诸事不宜")

    # 黄道/黑道 modifier
    if yellow_black["type"] == "黑道":
        ji.add("嫁娶")
        ji.add("入宅")
        ji.add("开市")
        ji.add("出行")

    # Mansion intersection
    mansion_ji = set(mansion.get("ji", []))
    mansion_yi = set(mansion.get("yi", []))

    # Final yi: jianchu yi minus what mansion says to avoid
    # Plus what mansion says is suitable (that isn't explicitly forbidden by jianchu)
    final_yi = sorted((yi - mansion_ji) | (mansion_yi - ji))
    final_ji = sorted(ji | mansion_ji)

    # Remove any activity in both yi and ji (yi takes precedence if it's in yi)
    # Actually: if something is in both yi and ji lists, remove from yi
    final_yi = [a for a in final_yi if a not in final_ji]

    # Combined score
    score = _compute_almanac_score(jianchu, yellow_black, mansion)

    return {
        "yi": final_yi,
        "ji": final_ji,
        "score": score,
    }


def _compute_almanac_score(jianchu: dict, yellow_black: dict, mansion: dict) -> int:
    """Compute overall almanac score (-100 to 100).

    Weighting:
      - 建除: 50% (dominant factor)
      - 黄道/黑道: 30% (major modifier)
      - 二十八宿: 20% (secondary modifier)
    """
    # Jianchu base: -80 to +55
    jc_score = jianchu["score"]

    # Yellow/Black: +35 or -35
    yb_score = 35 if yellow_black["is_auspicious"] else -35

    # Mansion quality based on number of yi vs ji activities
    mansion_yi_count = len(mansion.get("yi", []))
    mansion_ji_count = len(mansion.get("ji", []))
    if mansion_ji_count == 0 and mansion_yi_count >= 5:
        m_score = 15
    elif mansion_ji_count <= 1:
        m_score = 5
    elif mansion_ji_count <= 2:
        m_score = 0
    elif mansion_ji_count <= 3:
        m_score = -5
    else:
        m_score = -15

    total = jc_score * 0.50 + yb_score * 0.30 + m_score * 0.20 * 4
    return max(-100, min(100, int(total)))


def _ensure_daily_almanac_table(conn) -> None:
    """Create daily_almanac table if not exists."""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS daily_almanac (
            date                DATE PRIMARY KEY,
            jianchu_god         VARCHAR(4) NOT NULL,
            jianchu_index       INTEGER NOT NULL,
            yellow_black        VARCHAR(4) NOT NULL,
            yellow_black_god    VARCHAR(4) NOT NULL,
            mansion_28          VARCHAR(8) NOT NULL,
            mansion_group       VARCHAR(4) NOT NULL,
            mansion_luminary    VARCHAR(2) NOT NULL,
            pengzu_stem_taboo   VARCHAR(64),
            pengzu_branch_taboo VARCHAR(64),
            yi_activities       TEXT,
            ji_activities       TEXT,
            almanac_summary     VARCHAR(128),
            almanac_score       INTEGER DEFAULT 0,
            created_at          TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """)
    conn.execute("CREATE SEQUENCE IF NOT EXISTS seq_daily_almanac")


def compute_daily_almanac(d: "dt_date | None" = None, db_path: str = None) -> dict:
    """Compute full Chinese Almanac (黄历) for a given date.

    Caches result in daily_almanac table.

    Args:
        d: target date (default: today)
        db_path: optional database path override

    Returns dict with all almanac data.
    """
    if d is None:
        d = dt_date.today()

    # ── Calendar foundation ──
    cal = gregorian_to_ganzhi(d)
    month_zhi = cal["month_ganzhi"][1]
    day_zhi = cal["day_ganzhi"][1]
    day_stem = cal["day_ganzhi"][0]

    # ── 1. 建除十二神 ──
    jianchu = compute_jianchu(month_zhi, day_zhi)

    # ── 2. 黄道黑道 ──
    yellow_black = compute_yellow_black(month_zhi, day_zhi)

    # ── 3. 二十八宿 ──
    mansion = compute_mansion(d)

    # ── 4. 彭祖百忌 ──
    pengzu = compute_pengzu_taboos(day_stem, day_zhi)

    # ── 5. 宜忌综合 ──
    yiji = compute_yiji(jianchu, yellow_black, mansion)

    # ── Summary ──
    if jianchu["god"] == "破":
        summary = "破日，诸事不宜"
    elif yellow_black["is_auspicious"] and yiji["score"] >= 50:
        summary = f"黄道吉日，宜{yiji['yi'][0] if yiji['yi'] else '祭祀'}"
    elif yellow_black["is_auspicious"]:
        summary = f"黄道日，宜{yiji['yi'][0] if yiji['yi'] else '祭祀'}"
    elif yiji["score"] <= -50:
        summary = "黑道凶日，大事不宜"
    else:
        summary = f"黑道日，宜{yiji['yi'][0] if yiji['yi'] else '祭祀'}慎行"

    result = {
        "date": d.isoformat(),
        "year_ganzhi": cal["year_ganzhi"],
        "month_ganzhi": cal["month_ganzhi"],
        "day_ganzhi": cal["day_ganzhi"],
        "current_term": cal["current_term"],
        "zodiac": cal["zodiac"],
        "jianchu": jianchu,
        "yellow_black": yellow_black,
        "mansion_28": {
            "name": mansion["name"],
            "short": mansion["short"],
            "group": mansion["group"],
            "luminary": mansion["luminary"],
            "animal": mansion["animal"],
            "yi": mansion.get("yi", []),
            "ji": mansion.get("ji", []),
        },
        "pengzu_taboos": pengzu,
        "yi": yiji["yi"],
        "ji": yiji["ji"],
        "almanac_summary": summary,
        "almanac_score": yiji["score"],
    }

    # ── Cache in DB ──
    conn = init_db(db_path=db_path, read_only=False)
    try:
        _ensure_daily_almanac_table(conn)
        conn.execute("""
            INSERT OR REPLACE INTO daily_almanac
                (date, jianchu_god, jianchu_index, yellow_black, yellow_black_god,
                 mansion_28, mansion_group, mansion_luminary,
                 pengzu_stem_taboo, pengzu_branch_taboo,
                 yi_activities, ji_activities, almanac_summary, almanac_score)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, [
            d.isoformat(),
            jianchu["god"], jianchu["index"],
            yellow_black["type"], yellow_black["officer"],
            mansion["name"], mansion["group"], mansion["luminary"],
            pengzu["stem_taboo"], pengzu["branch_taboo"],
            json.dumps(yiji["yi"], ensure_ascii=False),
            json.dumps(yiji["ji"], ensure_ascii=False),
            summary, yiji["score"],
        ])
        conn.commit()
    finally:
        conn.close()

    return result


def get_daily_almanac(d: "dt_date | None" = None, db_path: str = None) -> dict:
    """Retrieve cached daily almanac or compute and cache if not present."""
    if d is None:
        d = dt_date.today()

    conn = init_db(db_path=db_path, read_only=True)
    try:
        # Check if table exists (DuckDB)
        tbl = conn.execute(
            "SELECT COUNT(*) FROM information_schema.tables WHERE table_name = 'daily_almanac'"
        ).fetchone()
        if tbl and tbl[0] > 0:
            row = conn.execute(
                "SELECT * FROM daily_almanac WHERE date = ?", [d.isoformat()]
            ).fetchone()
        else:
            row = None
    finally:
        conn.close()

    if row is None:
        return compute_daily_almanac(d=d, db_path=db_path)

    # Convert DB row to response dict
    yi = json.loads(row[10]) if row[10] else []
    ji = json.loads(row[11]) if row[11] else []

    return {
        "date": str(row[0]),
        "jianchu": {
            "god": row[1],
            "index": row[2],
            "description": JIANCHU_GODS[row[2]]["description"] if row[2] < len(JIANCHU_GODS) else "",
            "yi": JIANCHU_YIJI.get(row[1], {}).get("yi", []),
            "ji": JIANCHU_YIJI.get(row[1], {}).get("ji", []),
            "score": JIANCHU_SCORES.get(row[1], 0),
        },
        "yellow_black": {
            "officer": row[4],
            "type": row[3],
            "ji_xiong": "吉" if row[3] == "黄道" else "凶",
            "is_auspicious": row[3] == "黄道",
        },
        "mansion_28": {
            "name": row[5],
            "short": row[5][0] if row[5] else "",
            "group": row[6],
            "luminary": row[7],
            "yi": _MANSION_BY_SHORT.get(row[5][0], {}).get("yi", []) if row[5] else [],
            "ji": _MANSION_BY_SHORT.get(row[5][0], {}).get("ji", []) if row[5] else [],
        },
        "pengzu_taboos": {
            "stem_taboo": row[8] or "",
            "branch_taboo": row[9] or "",
        },
        "yi": yi,
        "ji": ji,
        "almanac_summary": row[12] or "",
        "almanac_score": row[13] or 0,
    }


def calibrate_mansion_offset(reference_pairs: list) -> int:
    """Calibrate the 28-mansion reference offset against known dates.

    Given a list of (date_str, expected_mansion_index) pairs, find the
    consistent offset that matches all reference dates.

    Example:
        calibrate_mansion_offset([
            ("2024-01-01", 0),   # 角木蛟
            ("2025-01-29", 7),   # 斗木獬 (Chinese New Year 2025)
        ])

    Returns the computed offset for _MANSION_REF_INDEX.
    """
    for offset in range(28):
        if all(
            (_MANSION_REF_INDEX + (dt_date.fromisoformat(date_str) - _MANSION_REF_DATE).days + offset) % 28 == expected_idx
            for date_str, expected_idx in reference_pairs
        ):
            return (_MANSION_REF_INDEX + offset) % 28
    raise ValueError("No consistent offset found; check reference data.")
