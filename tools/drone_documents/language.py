import re
from dataclasses import dataclass

from langdetect import DetectorFactory, LangDetectException, detect

DetectorFactory.seed = 0
HAN = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff]")
KANA = re.compile(r"[\u3040-\u30ff]")
HANGUL = re.compile(r"[\uac00-\ud7af]")
SIMPLIFIED = set("标记检查开关电机飞对这为说请启设载图录范护遥频围显线实无国")
TRADITIONAL = set("標記檢查開關電機飛對這為說請啟設載圖錄範護遙頻圍顯線實無國")
# Shared labels in installation diagrams and app screenshots are not translations.
TECHNICAL = re.compile(
    r"^(?:[\d\W]+|DJI(?:\s+(?:Fly|Pilot)(?:\s+App)?)?|"
    r"(?:DJI\s+)?(?:Air|Mavic|Mini|Matrice|Agras|Inspire|Phantom|Avata|Neo|Flip|"
    r"Lito|FlyCart|RoboMaster)(?:\s+[A-Za-z0-9.+-]+){0,4}|GNSS|GPS|USB|RC|RTK|"
    r"LED|LUT|DLog(?:-M)?|App|Mode\s*\d|ISO|EV|GHz|MHz|mAh|[A-Z]{1,4}-?\d{1,5}|"
    r"\d+(?:\.\d+)?\s*(?:mm|cm|m|g|kg|m/s|km/h|V|W|Wh|mAh|GHz|MHz))$",
)
MARKERS = {
    "CHS": "zh-Hans",
    "简体中文": "zh-Hans",
    "简体": "zh-Hans",
    "ZH-CN": "zh-Hans",
    "CHT": "zh-Hant",
    "繁體中文": "zh-Hant",
    "繁體": "zh-Hant",
    "ZH-TW": "zh-Hant",
    "EN": "en",
    "ENGLISH": "en",
    "JP": "ja",
    "JA": "ja",
    "日本語": "ja",
    "KR": "ko",
    "KO": "ko",
    "한국어": "ko",
    "DE": "de",
    "ES": "es",
    "FR": "fr",
    "IT": "it",
    "NL": "nl",
    "PT": "pt",
    "PT-BR": "pt",
    "RU": "ru",
    "AR": "ar",
    "TH": "th",
    "ID": "id",
    "TR": "tr",
    "PL": "pl",
    "CS": "cs",
    "DA": "da",
    "EL": "el",
    "FI": "fi",
    "HR": "hr",
    "HU": "hu",
    "NO": "no",
    "RO": "ro",
    "SV": "sv",
}
FOREIGN_MARKERS = set(MARKERS.values()) - {"zh-Hans"}


@dataclass
class LanguageDecision:
    language: str
    reason: str


def language(text: str) -> LanguageDecision:
    if KANA.search(text):
        return LanguageDecision("ja", "contains_kana")
    if HANGUL.search(text):
        return LanguageDecision("ko", "contains_hangul")
    han = HAN.findall(text)
    if han:
        simplified = sum(c in SIMPLIFIED - TRADITIONAL for c in text)
        traditional = sum(c in TRADITIONAL - SIMPLIFIED for c in text)
        if traditional > simplified:
            return LanguageDecision("zh-Hant", "traditional_characters")
        if simplified > traditional:
            return LanguageDecision("zh-Hans", "simplified_characters")
        if len(han) >= 10:
            try:
                detected = detect(text)
                if detected == "zh-tw":
                    return LanguageDecision("zh-Hant", "language_detector")
            except LangDetectException:
                pass
        return LanguageDecision("zh", "han_without_kana")
    if TECHNICAL.fullmatch(text.strip()) or not any(c.isalpha() for c in text):
        return LanguageDecision("und", "technical_or_numeric")
    if len(text.strip()) < 15:
        return LanguageDecision("und", "short_label")
    try:
        return LanguageDecision(detect(text), "language_detector")
    except LangDetectException:
        return LanguageDecision("und", "insufficient_text")


def page_markers(text: str) -> set[str]:
    return {
        MARKERS[line.strip().upper()]
        for line in text.splitlines()
        if line.strip().upper() in MARKERS
    }


def page_marker(text: str) -> str | None:
    markers = page_markers(text)
    # A multilingual table of contents contains several markers, not a language section.
    if len(markers) == 1:
        return markers.pop()
    return None


def chinese_dominant(text: str) -> bool:
    alpha = sum(c.isalpha() for c in text)
    return len(HAN.findall(text)) / max(alpha, 1) > 0.28 and len(KANA.findall(text)) < max(
        2, len(HAN.findall(text)) * 0.02
    )


def keep_line(text: str, *, dominant: bool, marker: str | None) -> bool:
    text = text.strip()
    if not text:
        return False
    if marker in FOREIGN_MARKERS:
        return False
    decision = language(text)
    if decision.language in {"ja", "ko", "zh-Hant"}:
        return False
    if dominant or marker == "zh-Hans":
        return True
    if decision.language in {"zh", "zh-Hans"}:
        return True
    return bool(TECHNICAL.fullmatch(text))


def shared_visual_text(text: str) -> bool:
    """Unknown language is insufficient: shared pages may contain only technical labels."""
    return all(not line.strip() or TECHNICAL.fullmatch(line.strip()) for line in text.splitlines())
