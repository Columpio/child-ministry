import fs from 'node:fs';
import path from 'node:path';

const root = path.resolve('../3.done_output_materials');
const out = path.resolve('public/generated');
fs.rmSync(out, { recursive: true, force: true });
fs.mkdirSync(out, { recursive: true });

const refs = {
  '01. Сотворение': 'Бытие 1',
  '02. Сотворение человека': 'Бытие 1-2',
  '03. Грехопадение': 'Бытие 3',
  '04. Как угодить Богу': 'Бытие 4',
  '05. Как победить гнев': 'Бытие 4',
  '06. Ковчег спасения': 'Бытие 6-8',
  '07. Бог верен Своим обещаниям': 'Бытие 7-9',
  '08. Как попасть на небеса': 'Бытие 11',
  '09. Что такое вера': 'Бытие 12-15',
  '10. Бог верен Своим обещаниям (рождение Исаака)': 'Бытие 18-21',
  '11. Испытание веры': 'Бытие 22',
  '12. Молитва веры — просить по воле Божьей': 'Бытие 24',
  '13. Временное и вечное': 'Бытие 25',
  '14. Важность благословения': 'Бытие 27',
  '15. Бог выбирает Иакова': 'Бытие 28',
  '16. Что посеешь, то и пожнёшь': 'Бытие 29',
  '17. Просить прощения не стыдно': 'Бытие 31-33',
  '18. Бог рядом (Иосиф)': 'Бытие 37-41',
  '19. У Бога всё под контролем (заключительный)': 'Бытие 42-50',
};

function slugifyPath(str) {
  return str
    .normalize('NFC')
    .replace(/[^a-zA-Z0-9а-яА-ЯёЁ._-]+/g, '-')
    .replace(/^-+|-+$/g, '');
}

function parseSlidesMarkdown(md, dir, courseDirName, lessonDirName) {
  const sections = md.split(/^##\s+/m).slice(1);
  return sections.map((sec, index) => {
    const lines = sec.split(/\r?\n/);
    const rawHeading = (lines.shift() || '').trim();
    const title = rawHeading.replace(/^(?:Слайд|Slide)\s*\d+\s*[.:—-]?\s*/i, '').trim() || rawHeading;
    const body = lines.join('\n').trim();

    let image = null;
    let imageAlt = '';
    const blocks = [];
    let currentList = null;

    const flushList = () => {
      if (currentList && currentList.length) {
        blocks.push({ type: 'list', items: currentList });
        currentList = null;
      }
    };

    for (const rawLine of body.split(/\r?\n/)) {
      const line = rawLine.trim();
      if (!line) {
        flushList();
        continue;
      }
      const imgMatch = line.match(/^!\[([^\]]*)\]\(\.\/([^)]+)\)$/);
      if (imgMatch) {
        flushList();
        imageAlt = imgMatch[1].trim();
        const fileName = imgMatch[2].trim();
        const srcFile = path.join(dir, fileName);
        if (fs.existsSync(srcFile)) {
          const courseSlug = String(courseDirName).match(/^\d+/)?.[0] || slugifyPath(courseDirName);
          const lessonSlug = String(lessonDirName).match(/^\d+/)?.[0] || slugifyPath(lessonDirName);
          const rel = `slides/${courseSlug}/${lessonSlug}/${fileName}`;
          const dest = path.join(out, rel);
          fs.mkdirSync(path.dirname(dest), { recursive: true });
          fs.copyFileSync(srcFile, dest);
          image = `generated/${rel}`;
        }
        continue;
      }
      if (line.startsWith('>')) {
        flushList();
        blocks.push({ type: 'quote', text: line.replace(/^>\s*/, '').trim() });
        continue;
      }
      if (/^[-*]\s+/.test(line)) {
        if (!currentList) currentList = [];
        currentList.push(line.replace(/^[-*]\s+/, '').trim());
        continue;
      }
      flushList();
      blocks.push({ type: 'paragraph', text: line });
    }
    flushList();

    return {
      number: index + 1,
      title,
      image,
      imageAlt,
      blocks,
    };
  });
}

const courses = [];
for (const ce of fs.readdirSync(root, { withFileTypes: true }).filter((x) => x.isDirectory())) {
  const course = ce.name;
  const lessons = [];
  for (const ld of fs.readdirSync(path.join(root, course), { withFileTypes: true }).filter((x) => x.isDirectory())) {
    const name = ld.name;
    const dir = path.join(root, course, name);
    const questionsPath = path.join(dir, 'QUESTIONS.json');
    const slidesMdPath = path.join(dir, 'SLIDES.md');

    const qjson = fs.existsSync(questionsPath)
      ? JSON.parse(fs.readFileSync(questionsPath, 'utf8'))
      : null;
    const parsedMdSlides = fs.existsSync(slidesMdPath)
      ? parseSlidesMarkdown(fs.readFileSync(slidesMdPath, 'utf8'), dir, course, name)
      : [];

    const single = Array.isArray(qjson?.questions);
    const count = single ? parsedMdSlides.length : Math.max(qjson?.slides?.length || 0, parsedMdSlides.length);
    const slides = [];
    for (let i = 0; i < count; i++) {
      const q = qjson?.slides?.[i] || {};
      const m = parsedMdSlides[i] || {};
      slides.push({
        number: q.number ?? m.number ?? i + 1,
        title: m.title || q.title || '',
        questions: q.questions || [],
        image: m.image || null,
        imageAlt: m.imageAlt || '',
        blocks: m.blocks || [],
        video_url: q.video_url || null,
      });
    }

    lessons.push({
      id: `${course}/${name}`,
      title: name.replace(/^\d+\.\s*/, ''),
      ref: refs[name] || '',
      slides,
      single,
      video_url: single ? (qjson.video_url || null) : null,
      questions: single ? qjson.questions : [],
      driveFolder: 'https://drive.google.com/drive/folders/1vD6ZgzvhjIZC0YWEeryQIzSyjPDqm51e',
    });
  }
  courses.push({ title: course.replace(/^\d+\.\s*/, ''), lessons });
}

fs.writeFileSync(path.join(out, 'course.json'), JSON.stringify(courses));
console.log('Generated', courses[0]?.lessons.length, 'lessons');
