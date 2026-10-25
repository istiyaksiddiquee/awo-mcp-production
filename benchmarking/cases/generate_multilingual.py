"""Generate benchmarking/cases/multilingual_expanded.json.

Mrunal's permutation engine from the datathon, retargeted to the AWO server's real tools:
the same request ("where can I find <service> in <district> (<postal code>)?") across
12 Berlin postal codes, 15 languages and dialects, and 8 service categories. Each case
expects a location-aware search (find_nearby with the postal code, or find_social_help
with the matching need); crisis and violence categories also require an emergency number
in the answer.

    python -m benchmarking.cases.generate_multilingual [--count 120]

The output is deterministic (stratified, no randomness), so regenerating gives the same file.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

OUTPUT = Path(__file__).resolve().parent / "multilingual_expanded.json"

BERLIN_PLZ_DISTRICTS: list[tuple[str, str, str]] = [
    ("10115", "Mitte", "Berlin-Mitte"),
    ("10997", "Kreuzberg", "Berlin-Kreuzberg"),
    ("14109", "Wannsee", "Berlin-Wannsee"),
    ("13353", "Wedding", "Berlin-Wedding"),
    ("10245", "Friedrichshain", "Berlin-Friedrichshain"),
    ("13187", "Pankow", "Berlin-Pankow"),
    ("13597", "Spandau", "Berlin-Spandau"),
    ("12555", "Köpenick", "Berlin-Köpenick"),
    ("10777", "Schöneberg", "Berlin-Schöneberg"),
    ("12043", "Neukölln", "Berlin-Neukölln"),
    ("10551", "Moabit", "Berlin-Moabit"),
    ("10365", "Lichtenberg", "Berlin-Lichtenberg"),
]

SERVICE_CATEGORIES: list[dict[str, str]] = [
    {
        "code": "Jugendberatung",
        "de": "Jugendberatung",
        "en": "youth counselling",
        "tr": "gençlik danışmanlığı",
        "ar": "استشارة الشباب",
        "pl": "poradnictwo młodzieżowe",
        "ru": "консультация для молодежи",
        "vi": "tư vấn thanh thiếu niên",
        "es": "asesoramiento juvenil",
        "fr": "conseil jeunesse",
        "ku": "şêwirmendiya ciwanan",
        "uk": "консультація для молоді",
        "fa": "مشاوره جوانان",
        "am": "የወጣቶች ምክር አገልግሎት",
        "leichte": "Hilfe für junge Menschen",
        "berlin": "Jugendberatung für meenen Kleenen",
        "audience": "Jugendliche",
    },
    {
        "code": "Schuldnerberatung",
        "de": "Schuldnerberatung",
        "en": "debt counselling",
        "tr": "borç danışmanlığı",
        "ar": "استشارات الديون",
        "pl": "doradztwo zadłużeniowe",
        "ru": "консультация по долгам",
        "vi": "tư vấn nợ",
        "es": "asesoramiento sobre deudas",
        "fr": "conseil en endettement",
        "ku": "şêwirmendiya deynan",
        "uk": "консультація щодо боргів",
        "fa": "مشاوره بدهی",
        "am": "የዕዳ ምክር አገልግሎት",
        "leichte": "Hilfe bei Geld-Sorgen und Schulden",
        "berlin": "Schuldnerberatung wegen Miete",
        "audience": "Erwachsene / Familien",
    },
    {
        "code": "Frauenhaus",
        "de": "Frauenhaus und Gewaltschutz",
        "en": "women's shelter and domestic violence support",
        "tr": "kadın sığınma evi",
        "ar": "مأوى للنساء وحماية من العنف",
        "pl": "schronisko dla kobiet",
        "ru": "приют для женщин",
        "vi": "nhà tạm lánh cho phụ nữ",
        "es": "refugio para mujeres",
        "fr": "foyer pour femmes",
        "ku": "stargeha jinan",
        "uk": "притулок для жінок",
        "fa": "پناهگاه زنان",
        "am": "የሴቶች መጠለያ",
        "leichte": "Schutz-Haus für Frauen",
        "berlin": "Frauenhaus",
        "audience": "Frauen",
    },
    {
        "code": "Suchtberatung",
        "de": "Suchtberatung",
        "en": "addiction counselling",
        "tr": "bağımlılık danışmanlığı",
        "ar": "استشارات الإدمان",
        "pl": "poradnia uzależnień",
        "ru": "наркологическая консультация",
        "vi": "tư vấn cai nghiện",
        "es": "asesoramiento sobre adicciones",
        "fr": "centre d'addictologie",
        "ku": "şêwirmendiya tiryakê",
        "uk": "консультація щодо залежностей",
        "fa": "مشاوره ترک اعتیاد",
        "am": "የሱስ ምክር አገልግሎት",
        "leichte": "Hilfe bei Sucht",
        "berlin": "Suchtberatung",
        "audience": "Suchtkranke",
    },
    {
        "code": "Familienberatung",
        "de": "Familienberatung",
        "en": "family counselling",
        "tr": "aile danışmanlığı",
        "ar": "الاستشارات الأسرية",
        "pl": "poradnictwo rodzinne",
        "ru": "семейная консультация",
        "vi": "tư vấn gia đình",
        "es": "orientación familiar",
        "fr": "conseil familial",
        "ku": "şêwirmendiya malbatê",
        "uk": "сімейна консультація",
        "fa": "مشاوره خانواده",
        "am": "የቤተሰብ ምክር",
        "leichte": "Beratung für Familien",
        "berlin": "Familienberatung",
        "audience": "Familien",
    },
    {
        "code": "Migrationsberatung",
        "de": "Migrationsberatung",
        "en": "migration and refugee legal counselling",
        "tr": "göçmenlik danışmanlığı",
        "ar": "استشارات الهجرة واللاجئين",
        "pl": "doradztwo migracyjne",
        "ru": "миграционная консультация",
        "vi": "tư vấn di trú",
        "es": "asesoría de migración",
        "fr": "conseil en migration",
        "ku": "şêwirmendiya koçberiyê",
        "uk": "консультація для біженців та мігрантів",
        "fa": "مشاوره مهاجرت و پناهندگی",
        "am": "የስደተኞች ምክር",
        "leichte": "Hilfe für Menschen aus anderen Ländern",
        "berlin": "Migrationsberatung",
        "audience": "Geflüchtete / Migrant*innen",
    },
    {
        "code": "Seniorenhilfe",
        "de": "Seniorenhilfe und Pflegeberatung",
        "en": "senior care and elderly support",
        "tr": "yaşlı bakım danışmanlığı",
        "ar": "رعاية المسنين",
        "pl": "opieka nad seniorami",
        "ru": "помощь пожилым людям",
        "vi": "hỗ trợ người cao tuổi",
        "es": "ayuda para personas mayores",
        "fr": "aide aux personnes âgées",
        "ku": "alîkariya kal û pîran",
        "uk": "допомога літнім людям",
        "fa": "حمایت از سالمندان",
        "am": "የአረጋውያን ድጋፍ",
        "leichte": "Hilfe für ältere Menschen",
        "berlin": "Seniorenhilfe für Oma",
        "audience": "Senior*innen",
    },
    {
        "code": "Krisenintervention",
        "de": "Psychosoziale Krisenintervention",
        "en": "psychosocial crisis intervention",
        "tr": "psikososyal kriz müdahalesi",
        "ar": "التدخل في الأزمات النفسية",
        "pl": "interwencja kryzysowa",
        "ru": "кризисная психологическая помощь",
        "vi": "can thiệp khủng hoảng tâm lý",
        "es": "intervención en crisis psicosocial",
        "fr": "intervention de crise psychosociale",
        "ku": "destwerdana krîza derûnî",
        "uk": "психологічна кризова допомога",
        "fa": "مداخله در بحران روانی",
        "am": "የሥነ ልቦና ቀውስ ድጋፍ",
        "leichte": "Sofort-Hilfe bei seelischen Krisen",
        "berlin": "Krisenhilfe",
        "audience": "Menschen in Krisen",
    },
]

LANGUAGE_TEMPLATES: list[dict[str, str]] = [
    {
        "key": "de",
        "lang_label": "Standard German (Hochdeutsch)",
        "demo": "Local German Residents",
        "iso": "de",
        "template": "Wo finde ich eine verifizierte Stelle für {service} im Bezirk {locality} (PLZ {plz})?",
    },
    {
        "key": "en",
        "lang_label": "English",
        "demo": "Expat / International Resident",
        "iso": "en",
        "template": "Where can I find verified {service} in {locality} (postal code {plz})?",
    },
    {
        "key": "tr",
        "lang_label": "Turkish (Türkçe)",
        "demo": "Turkish-German Community",
        "iso": "tr",
        "template": "{locality} ({plz}) bölgesinde Türkçe destek veren {service} nerede bulabilirim?",
    },
    {
        "key": "ar",
        "lang_label": "Syrian Arabic (الشامية / العربية)",
        "demo": "Arabic-speaking Newcomers / Refugees",
        "iso": "ar",
        "template": "أين يمكنني العثور على {service} في منطقة {locality} (الرمز البريدي {plz})؟",
    },
    {
        "key": "pl",
        "lang_label": "Polish (Polski)",
        "demo": "Polish Cross-border / EU Workers",
        "iso": "pl",
        "template": "Gdzie w dzielnicy {locality} ({plz}) znajdę {service}?",
    },
    {
        "key": "ru",
        "lang_label": "Russian (Русский)",
        "demo": "Russian-speaking Diaspora",
        "iso": "ru",
        "template": "Где в районе {locality} (индекс {plz}) можно найти {service}?",
    },
    {
        "key": "vi",
        "lang_label": "Vietnamese (Tiếng Việt)",
        "demo": "Vietnamese Community",
        "iso": "vi",
        "template": "Tôi có thể tìm {service} tại quận {locality} (mã bưu điện {plz}) ở đâu?",
    },
    {
        "key": "es",
        "lang_label": "Spanish (Español)",
        "demo": "Spanish-speaking Expats / Students",
        "iso": "es",
        "template": "¿Dónde puedo encontrar un centro de {service} en {locality} (código postal {plz})?",
    },
    {
        "key": "fr",
        "lang_label": "French (Français)",
        "demo": "Francophone African / European Residents",
        "iso": "fr",
        "template": "Où puis-je trouver un service de {service} à {locality} (code postal {plz}) ?",
    },
    {
        "key": "ku",
        "lang_label": "Kurmanji Kurdish (Kurmancî)",
        "demo": "Kurdish Community",
        "iso": "ku",
        "template": "Li {locality} ({plz}) ez dikarim li ku derê {service} peyda bikim?",
    },
    {
        "key": "uk",
        "lang_label": "Ukrainian (Українська)",
        "demo": "Ukrainian Displaced Persons",
        "iso": "uk",
        "template": "Де в районі {locality} ({plz}) є {service} для українців?",
    },
    {
        "key": "fa",
        "lang_label": "Farsi / Dari (فارسی)",
        "demo": "Afghan / Iranian Newcomers",
        "iso": "fa",
        "template": "کجا می‌توانم در منطقه {locality} (کد پستی {plz}) مرکز {service} پیدا کنم؟",
    },
    {
        "key": "am",
        "lang_label": "Amharic (አማርኛ)",
        "demo": "East African Community",
        "iso": "am",
        "template": "በ{locality} ({plz}) አካባቢ {service} የት ማግኘት እችላለሁ?",
    },
    {
        "key": "leichte",
        "lang_label": "Leichte Sprache (German Easy Read)",
        "demo": "People with Learning Difficulties",
        "iso": "de-simple",
        "template": "Ich suche {service} in {locality} ({plz}). Bitte erklären Sie es in Leichter Sprache.",
    },
    {
        "key": "berlin",
        "lang_label": "Berlinerisch (Berlin Dialect)",
        "demo": "Native Berlin Dialect Speakers",
        "iso": "de-berlin",
        "template": "Sach mal, wo jibt et denn hier in {locality} ({plz}) 'ne vernünftige {service}?",
    },
]


# Category code -> find_social_help need (None: no matching need; hotline required instead).
CATEGORY_NEEDS = {
    "Jugendberatung": "family",
    "Schuldnerberatung": "debt",
    "Frauenhaus": None,
    "Suchtberatung": "addiction",
    "Familienberatung": "family",
    "Migrationsberatung": "migration",
    "Seniorenhilfe": "elderly",
    "Krisenintervention": None,
}

HOTLINE_CATEGORIES = {"Frauenhaus", "Krisenintervention"}


def build_case(index: int, plz: tuple[str, str, str], language: dict, category: dict) -> dict:
    code, (postal_code, district, locality) = category["code"], plz
    service = category.get(language["key"], category["en"])
    need = CATEGORY_NEEDS[code]
    by_tool = {"find_nearby": {"location": postal_code}}
    tools = ["find_nearby"]
    if need:
        tools.append("find_social_help")
        by_tool["find_social_help"] = {"need": need}
    tools += ["search_directory", "search_service_candidates"]
    return {
        "id": f"ml-{index:03d}-{language['key']}-{code.lower()}-{postal_code}",
        "prompt": language["template"].format(service=service, locality=district, plz=postal_code),
        "expect_tool": tools,
        "expect_args_by_tool": by_tool,
        "requires_hotline": code in HOTLINE_CATEGORIES,
        "language": language["lang_label"],
        "persona": language["demo"],
        "section": "Multilingual local search",
        "source": "datathon benchmarking permutation engine",
        "english_translation": f"Where can I find {category['en']} in {locality} (postal code {postal_code})?",
    }


def generate(count: int = 120) -> list[dict]:
    """Stratified: language, district and category rotate at different steps."""
    cases = []
    for i in range(count):
        language = LANGUAGE_TEMPLATES[i % len(LANGUAGE_TEMPLATES)]
        plz = BERLIN_PLZ_DISTRICTS[(i * 5) % len(BERLIN_PLZ_DISTRICTS)]
        category = SERVICE_CATEGORIES[(i * 3 + i // len(LANGUAGE_TEMPLATES)) % len(SERVICE_CATEGORIES)]
        cases.append(build_case(i + 1, plz, language, category))
    return cases


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate the multilingual benchmark suite.")
    parser.add_argument("--count", type=int, default=120)
    parser.add_argument("--output", default=str(OUTPUT))
    args = parser.parse_args()
    cases = generate(args.count)
    Path(args.output).write_text(json.dumps(cases, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {len(cases)} cases to {args.output}")


if __name__ == "__main__":
    main()
