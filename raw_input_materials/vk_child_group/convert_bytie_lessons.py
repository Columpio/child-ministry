# -*- coding: utf-8 -*-
"""
Конвертирует дамп темы VK «Бытие. Цикл уроков для детей. Красная нить»
в набор markdown-файлов: один файл на урок.

Вложения (фото/документы) НЕ копируются — в markdown вставляются
относительные ссылки на файлы в исходной папке дампа.
"""
import html
import json
import os
import re
import sys

BASE = os.path.dirname(os.path.abspath(__file__))
TOPIC_DIR_NAME = "01_50314759_Бытие. Цикл уроков для детей Красная нить"
TOPIC_DIR = os.path.join(BASE, "vk_board", TOPIC_DIR_NAME)
OUT_DIR = os.path.join(BASE, "uroki_bytie_krasnaya_nit")

# Границы уроков: (номер урока, индекс первого поста, индекс последнего поста включительно, название)
LESSONS = [
    (1, 1, 4, "Сотворение"),
    (2, 5, 7, "Грехопадение"),
    (3, 8, 11, "Как угодить Богу"),
    (4, 12, 13, "Как победить гнев"),
    (5, 14, 18, "Ковчег спасения"),
    (6, 19, 24, "Бог верен Своим обещаниям"),
    (7, 25, 28, "Как попасть на небеса"),
    (8, 29, 33, "Что такое вера"),
    (9, 34, 38, "Бог верен Своим обещаниям (рождение Исаака)"),
    (10, 39, 41, "Испытание веры"),
    (11, 42, 46, "Молитва веры — начало любого дела"),
    (12, 47, 49, "Временное и вечное"),
    (13, 50, 51, "Важность благословения"),
    (14, 52, 54, "Бог выбирает Иакова"),
    (15, 55, 59, "Что посеешь, то и пожнёшь"),
    (16, 60, 62, "Просить прощения не стыдно"),
    (17, 63, 67, "Бог рядом! (Иосиф)"),
    (18, 68, 73, "У Бога всё под контролем! (заключительный)"),
]
INTRO_IDX = 0

# Полезное из комментариев темы, вмёрженное в соответствующие уроки:
# урок -> (якорная подстрока в тексте урока, после которой вставить, что вставить)
EXTRA_NOTES = {
    14: (
        "скрепленных между собой с помощью брадс.)",
        "\n(Крепёж называется «брадсы». Они бывают разных размеров, цветов и форм. "
        "Заказать можно на Ozon или Wildberries.)\n\n"
        "![брадсы](../vk_board/01_50314759_Бытие.%20Цикл%20уроков%20для%20детей%20Красная%20нить/photos/-210485732_457255460.jpg)",
    ),
}

TRANSLIT = {
    'а': 'a', 'б': 'b', 'в': 'v', 'г': 'g', 'д': 'd', 'е': 'e', 'ё': 'yo',
    'ж': 'zh', 'з': 'z', 'и': 'i', 'й': 'y', 'к': 'k', 'л': 'l', 'м': 'm',
    'н': 'n', 'о': 'o', 'п': 'p', 'р': 'r', 'с': 's', 'т': 't', 'у': 'u',
    'ф': 'f', 'х': 'h', 'ц': 'c', 'ч': 'ch', 'ш': 'sh', 'щ': 'sch',
    'ъ': '', 'ы': 'y', 'ь': '', 'э': 'e', 'ю': 'yu', 'я': 'ya',
}

def slugify(title):
    s = title.lower()
    s = ''.join(TRANSLIT.get(ch, ch) for ch in s)
    s = re.sub(r'[^a-z0-9]+', '-', s).strip('-')
    return s

def rel_link(abs_path):
    """Относительный путь от OUT_DIR к файлу, с URL-кодированием пробелов."""
    rel = os.path.relpath(abs_path, OUT_DIR).replace(os.sep, '/')
    return rel.replace(' ', '%20').replace('(', '%28').replace(')', '%29')

def photo_file(href):
    # href вида /photo-210485732_457251355?list=... -> photos/-210485732_457251355.jpg
    m = re.match(r'/photo(-?\d+_\d+)', href)
    if not m:
        return None
    p = os.path.join(TOPIC_DIR, 'photos', m.group(1) + '.jpg')
    return p if os.path.exists(p) else None

def doc_file(href, doc_files):
    name = doc_files.get(href)
    if not name:
        return None, None
    p = os.path.join(TOPIC_DIR, 'docs', name)
    return (p if os.path.exists(p) else None), name

def video_url(href):
    return 'https://vk.com' + href.split('?')[0]

def video_label(label):
    label = html.unescape(label or '')
    label = re.sub(r'^Видео\s+', '', label)
    label = re.sub(r'\s*длительностью\s*', ' (', label)
    if label.endswith(')') is False and ' (' in label:
        label += ')'
    return label.strip()

def render_post(post, doc_files):
    parts = []
    text = (post.get('text') or '').strip()
    # убрать служебные строки-«продолжения», оставшиеся от разбивки на посты в VK
    text = re.sub(r'^\(?\s*Продолжение урока[^\n]*\)?\s*\n', '', text, flags=re.I | re.M)
    text = text.strip()
    if text:
        parts.append(text + '\n')
    for ph in post.get('photos', []):
        f = photo_file(ph['href'])
        if f:
            parts.append(f"![фото]({rel_link(f)})")
        else:
            parts.append(f"[фото (внешняя ссылка)]({ph.get('thumb', ph['href'])})")
    if post.get('photos'):
        parts.append('')
    for dc in post.get('docs', []):
        f, name = doc_file(dc['href'], doc_files)
        if f:
            parts.append(f"📎 [{name}]({rel_link(f)})")
        else:
            nm = name or html.unescape(dc.get('name', 'документ'))
            parts.append(f"📎 {nm} *(файл не найден локально)*")
    if post.get('docs'):
        parts.append('')
    for vd in post.get('videos', []):
        parts.append(f"🎬 [{video_label(vd.get('label'))}]({video_url(vd['href'])})")
    if post.get('videos'):
        parts.append('')
    return '\n'.join(parts)

# ---------- разметка Markdown ----------

# нумерованные разделы плана урока -> '## N. Название'
SECTION_RE = re.compile(
    r'^(\d+)\s*\.\s*'
    r'(молитва\s+благодарения|молитва|повторение|заинтересуй|вступление|'
    r'библейская\s+история|закрепление|обсуждение|золотой\s+стих|применение|'
    r'поделка\s+к\s+уроку|поделка|игра)'
    r'\s*([.:])?\s*(.*?)\s*$', re.I)

# поля шапки урока -> '**Тема:** ...'
HEADER_RE = re.compile(
    r'^(тема|истина|цель|библ\.?\s*история|библейская\s+история|'
    r'золотой\s+стих|план\s+урока)\s*[:.]?\s*(.*?)\s*$', re.I)
HEADER_LABELS = {
    'тема': 'Тема', 'истина': 'Истина', 'цель': 'Цель',
    'библ.история': 'Библейская история',
    'библейская история': 'Библейская история',
    'золотой стих': 'Золотой стих', 'план урока': 'План урока',
}

# строки-заголовки доп. материалов -> '### ...' (значение: (заголовок, сколько след. строк поглотить))
EXTRAS = {
    'Комментарий для учителей.': ('Комментарий для учителей', 0),
    'Идеи наглядности к уроку 2.': ('Идеи наглядности', 0),
    'Задания для детей к уроку 2': ('Задания для детей', 0),
    'Поделка к уроку 3': ('Поделка', 0),
    'Идея наглядности к уроку 3.': ('Идея наглядности', 0),
    'Наглядное пособие "Авраам отправляется в путешествие"':
        ('Наглядное пособие «Авраам отправляется в путешествие»', 0),
    'Песня к уроку на закрепление.': ('Песня на закрепление', 0),
    'Идея поделки-наглядности к истории о невесте для Исаака':
        ('Идея поделки-наглядности', 0),
    'К уроку 11. Применение. Эксперимент о молитве.': ('Эксперимент о молитве', 0),
    'К уроку 12': ('Итог игры «Временное и вечное»', 0),
    'К уроку 15': ('Встреча Иакова и Рахиль', 1),
    '"Классики- путь Иосифа."': ('«Классики — путь Иосифа»', 0),
    'Игра на повторение Библейской истории': ('Игра на повторение «Иосиф в колодце»', 1),
}

# короткие строки вида 'Игра "..."' / 'Поделка «...»' внутри разделов -> жирные
GAME_TITLE_RE = re.compile(r'^(Игра|Поделка)\s+[«"\'].*[^.!?]$')

# нумерованные шаги-подзаголовки внутри «Библейской истории» -> жирные;
# ключ — номер урока, значение — regex (шаблоны проверены вручную по тексту)
STORY_STEP_RES = {
    14: re.compile(r'^\d+\s*\.\s*Сформировать[^?]{0,50}$'),
    17: re.compile(r'^\d+\s*\.[^?]{0,52}$'),
    18: re.compile(r'^\d+\s*\.\s*Рисуем[^?]*$'),
}

# точечные правки исходного текста перед разметкой
RAW_FIXES = [
    # это предложение, а не заголовок раздела — отделяем заголовок от текста
    ('8. Закрепление урока я провожу во время поделки.',
     '8. Закрепление.\nЗакрепление урока я провожу во время поделки.'),
]


def _sentence_case(s):
    s = re.sub(r'\s+', ' ', s.strip())
    return s[0].upper() + s[1:].lower()


def format_lesson(body, lesson_num):
    """Превращает «сплошной текст» урока в размеченный Markdown."""
    raw_lines = [l.rstrip() for l in body.split('\n')]
    items = []  # (kind, text); kind: h2, h3, text, olist, blist
    in_story = False
    in_header = True  # поля шапки (Тема/Истина/...) распознаём только до первого раздела
    first_line_done = False
    i = 0
    while i < len(raw_lines):
        line = raw_lines[i].strip()
        i += 1
        if not line:
            continue
        # выкинуть дублирующую заголовок первую строку вида 'УРОК № 5'
        if not first_line_done:
            first_line_done = True
            if re.match(r'^урок\s*№?\s*\d+\b', line, re.I):
                continue
        # заголовки доп. материалов
        if line in EXTRAS:
            heading, consume = EXTRAS[line]
            for _ in range(consume):
                while i < len(raw_lines) and not raw_lines[i].strip():
                    i += 1
                i += 1
            items.append(('h3', '### ' + heading))
            in_header = False
            continue
        # нумерованные разделы плана
        m = SECTION_RE.match(line)
        if m:
            num, sec, punct, rest = m.group(1), m.group(2), m.group(3), m.group(4)
            title = f"{num}. {_sentence_case(sec)}"
            if rest:
                title += ('. ' if punct == '.' else ': ' if punct == ':' else ' ') + rest
            in_story = sec.lower().startswith('библейская')
            items.append(('h2', '## ' + title))
            in_header = False
            continue
        # поля шапки
        m = HEADER_RE.match(line)
        if m and in_header and not re.match(r'^\d', line):
            label = re.sub(r'\s+', ' ', m.group(1).lower()).replace('библ. история', 'библ.история')
            rest = m.group(2)
            items.append(('text', f"**{HEADER_LABELS[label]}:** {rest}".rstrip()))
            continue
        # жирные подзаголовки игр/поделок внутри разделов
        if GAME_TITLE_RE.match(line) and len(line) < 60 and '. ' not in line:
            items.append(('text', f"**{line}**"))
            continue
        # нумерованные шаги-подзаголовки внутри библейской истории (наглядные предметы и т.п.)
        step_re = STORY_STEP_RES.get(lesson_num)
        if in_story and step_re and step_re.match(line):
            line = re.sub(r'^(\d+)\s*\.\s*', r'\1. ', line)
            items.append(('text', f"**{line}**"))
            continue
        # списки: '1. текст', '1.текст', '-текст'
        m = re.match(r'^(\d+)\s*\.\s*(\S.*)$', line)
        if m:
            items.append(('olist', f"{m.group(1)}. {m.group(2)}"))
            continue
        m = re.match(r'^[-•]\s*(\S.*)$', line)
        if m:
            items.append(('blist', f"- {m.group(1)}"))
            continue
        items.append(('text', line))

    # сборка: между блоками пустая строка; текстовые строки — с жёстким переносом
    blocks, cur_kind, cur = [], None, []
    for kind, text in items:
        if kind == cur_kind and kind in ('text', 'olist', 'blist'):
            cur.append(text)
        else:
            if cur:
                blocks.append((cur_kind, cur))
            cur_kind, cur = kind, [text]
    if cur:
        blocks.append((cur_kind, cur))
    parts = []
    for kind, lines in blocks:
        if kind == 'text':
            parts.append('  \n'.join(lines))
        else:
            parts.append('\n'.join(lines))
    return '\n\n'.join(parts)


def main():
    with open(os.path.join(TOPIC_DIR, 'posts.json'), encoding='utf-8') as f:
        data = json.load(f)
    posts = data['posts']
    doc_files = data['doc_files']
    os.makedirs(OUT_DIR, exist_ok=True)

    # sanity check: первые посты уроков действительно похожи на начало урока
    for num, start, end, title in LESSONS:
        t = posts[start]['text']
        assert re.search(rf'урок\s*№?\s*{num}\b', t, re.I), \
            f"пост {start} не похож на начало урока {num}: {t[:60]!r}"

    toc = []
    for num, start, end, title in LESSONS:
        fname = f"urok-{num:02d}-{slugify(title)}.md"
        toc.append((num, title, fname))
        raw = '\n'.join(render_post(posts[i], doc_files) for i in range(start, end + 1))
        for old, new in RAW_FIXES:
            raw = raw.replace(old, new)
        if num in EXTRA_NOTES:
            anchor, note = EXTRA_NOTES[num]
            assert anchor in raw, f"якорь для примечания урока {num} не найден"
            raw = raw.replace(anchor, anchor + note, 1)
        out = f"# Урок {num}. {title}\n\n" + format_lesson(raw, num)
        out = re.sub(r'\n{3,}', '\n\n', out)
        with open(os.path.join(OUT_DIR, fname), 'w', encoding='utf-8') as f:
            f.write(out.rstrip() + '\n')

    # README и kommentarii больше не генерируются; удалить, если остались от прошлого запуска
    for stale in ('README.md', 'kommentarii.md'):
        p = os.path.join(OUT_DIR, stale)
        if os.path.exists(p):
            os.remove(p)

    print(f"OK: {len(toc)} уроков -> {OUT_DIR}")

if __name__ == '__main__':
    main()
