from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class KeywordRuleClassifier:
    """可替换的规则分类器；以后可实现同名 classify_* 接口接入 NLP/LLM。"""

    video_rules = (
        ("医生科普", ("医生", "医师", "医院", "医学", "科普", "门诊", "主任", "临床")),
        ("药物相关", ("药", "用药", "剂量", "处方", "副作用", "吃什么药")),
        ("疾病咨询", ("疾病", "症状", "疼", "检查", "诊断", "复发")),
        ("医疗经历", ("手术", "住院", "康复", "治疗经历", "确诊")),
        ("健康知识", ("健康", "饮食", "运动", "急救", "预防", "养生")),
    )
    comment_rules = (
        ("询问药物", ("什么药", "吃药", "用药", "剂量", "副作用", "药吗")),
        ("询问治疗", ("怎么治", "治疗", "手术", "挂什么科", "能治", "怎么办")),
        ("咨询症状", ("症状", "疼", "痛", "不舒服", "发烧", "咳嗽", "正常吗")),
        ("分享经历", ("我也是", "我之前", "我的", "经历", "确诊", "做过")),
        ("感谢医生", ("谢谢", "感谢", "辛苦", "讲得好", "收藏了")),
        ("质疑/反对", ("不对", "骗人", "误导", "胡说", "反对", "假的")),
    )

    @staticmethod
    def _classify(text: str, rules: tuple[tuple[str, tuple[str, ...]], ...]) -> str:
        normalized = (text or "").lower()
        for label, keywords in rules:
            if any(word.lower() in normalized for word in keywords):
                return label
        return "其他"

    def classify_video(self, title: str, author: str) -> str:
        return self._classify(f"{title} {author}", self.video_rules)

    def classify_comment(self, text: str) -> str:
        return self._classify(text, self.comment_rules)
