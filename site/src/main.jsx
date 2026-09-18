import React, { useEffect, useMemo, useState } from 'react';
import { createRoot } from 'react-dom/client';
import { BookOpen, ChevronLeft, ChevronRight, Home, Star, CheckCircle, Trophy, LockKeyhole } from 'lucide-react';
import './style.css';

const STORAGE_KEY = 'bible-course-progress-v1';
const base = import.meta.env.BASE_URL;
const pathFor = (path) => `${base}${path.replace(/^\//, '')}`;
function readProgress() { try { return JSON.parse(localStorage.getItem(STORAGE_KEY) || '{}'); } catch { return {}; } }
function go(hash) { window.location.hash = hash; }
function parseRoute() {
  const hash = window.location.hash || '#/';
  const parts = hash.replace(/^#\/?/, '').split('/').filter(Boolean);
  if (!parts.length) return { page: 'home' };
  if (parts[0] === 'testaments') return { page: 'testaments' };
  if (parts[0] === 'old-testament' && parts[1] === 'genesis' && parts[2] === 'lesson') {
    return { page: parts[4] === 'result' ? 'result' : 'lesson', lesson: Number(parts[3]) || 0, slide: Number(parts[5]) || 0 };
  }
  if (parts[0] === 'old-testament' && parts[1] === 'genesis') return { page: 'lessons' };
  if (parts[0] === 'old-testament') return { page: 'books' };
  return { page: 'home' };
}

function App() {
  const [data, setData] = useState([]);
  const [route, setRoute] = useState(parseRoute);
  const [answers, setAnswers] = useState({});
  const [result, setResult] = useState(null);
  const [showAllAnswers, setShowAllAnswers] = useState(false);
  useEffect(() => { fetch(`${base}generated/course.json`).then((r) => r.json()).then(setData); }, []);
  useEffect(() => { const sync = () => setRoute(parseRoute()); window.addEventListener('hashchange', sync); return () => window.removeEventListener('hashchange', sync); }, []);
  const lessons = data[0]?.lessons || [];
  const lesson = route.lesson === undefined ? null : lessons[route.lesson];
  const slideIndex = route.slide || 0;
  const slide = lesson?.slides?.[slideIndex];
  const progressKey = lesson?.id ?? route.lesson;

  useEffect(() => {
    if (!lesson) return;
    const saved = readProgress()[progressKey];
    setAnswers(saved?.answers || {});
    setResult(route.page === 'result' ? (saved?.result || null) : null);
    setShowAllAnswers(false);
  }, [progressKey, route.page, lesson]);
  useEffect(() => {
    if (!lesson) return;
    const saved = readProgress(); saved[progressKey] = { slide: slideIndex, answers, result };
    localStorage.setItem(STORAGE_KEY, JSON.stringify(saved));
  }, [progressKey, lesson, slideIndex, answers, result]);

  const openLesson = (index) => { const saved = readProgress()[lessons[index]?.id ?? index]; go(`#/old-testament/genesis/lesson/${index}/slide/${saved?.slide || 0}`); };
  const choose = (q, i, j) => setAnswers((a) => ({ ...a, [`${slideIndex}-${i}`]: { selected: j, question: q } }));
  const canNext = () => !slide?.questions?.some((_, i) => answers[`${slideIndex}-${i}`] === undefined);
  const finish = () => {
    const items = []; lesson.slides.forEach((sl, si) => sl.questions?.forEach((q, qi) => { const a = answers[`${si}-${qi}`]; const correct = q.options?.findIndex((o) => o.correct === true || o.correct === 'true') ?? -1; items.push({ prompt: q.prompt, selectedText: a ? q.options[a.selected]?.text : 'Не выбран', correctText: correct >= 0 ? q.options[correct].text : 'Правильный ответ не указан', ok: a?.selected === correct }); }));
    setResult({ items, score: items.filter((x) => x.ok).length }); go(`#/old-testament/genesis/lesson/${route.lesson}/result`);
  };

  if (route.page === 'home') return <Landing />;
  if (route.page === 'testaments') return <><Header /><main className="selection"><button className="backLink" onClick={() => go('#/')}>← На главную</button><h1>Выбери Завет</h1><p className="lead">С какой части Библии начнём путешествие?</p><div className="choiceGrid"><button className="choiceCard old" onClick={() => go('#/old-testament')}><span>📜</span><strong>Ветхий Завет</strong><small>Истории от Сотворения мира</small></button><button className="choiceCard disabled" disabled><span>✝️</span><strong>Новый Завет</strong><small>Скоро будет доступен</small><LockKeyhole /></button></div></main></>;
  if (route.page === 'books') return <><Header /><main className="selection"><button className="backLink" onClick={() => go('#/testaments')}>← К Заветам</button><h1>Ветхий Завет</h1><p className="lead">Выбери книгу для изучения</p><div className="bookGrid"><button className="bookCard" onClick={() => go('#/old-testament/genesis')}><span className="num">01</span><strong>Бытие</strong><small>История начала всего</small><Star className="star" /></button>{['Исход', 'Левит', 'Числа', 'Второзаконие'].map((name) => <button className="bookCard disabled" disabled key={name}><span className="num">—</span><strong>{name}</strong><small>Материалы готовятся</small><LockKeyhole /></button>)}</div></main></>;
  if (!lesson && (route.page === 'lessons' || route.page === 'lesson')) return <><Header /><main className="selection"><button className="backLink" onClick={() => go('#/old-testament')}>← К книгам</button><h1>Книга Бытие</h1><p className="lead">Выбери урок</p><div className="grid">{lessons.map((x, i) => <button className={`lesson ${!x.slides.length ? 'lesson-disabled' : ''}`} key={x.id} disabled={!x.slides.length} onClick={() => openLesson(i)}><span className="num">{String(i + 1).padStart(2, '0')}</span><strong>{x.title}</strong><small>{x.slides.length ? `${x.slides.length} слайдов` : 'Материалы готовятся'}</small><Star className="star" /></button>)}</div></main></>;
  if (route.page === 'result' && result) return <Result lesson={lesson} result={result} showAllAnswers={showAllAnswers} setShowAllAnswers={setShowAllAnswers} />;
  if (!lesson || !slide) return null;
  return <Lesson lesson={lesson} slide={slide} slideIndex={slideIndex} answers={answers} choose={choose} canNext={canNext} finish={finish} />;
}

function Landing() {
  const [info, setInfo] = useState(null);
  const closeInfo = () => setInfo(null);
  return <main className="landingExact">
    <div className={`landingExactNav ${info ? 'isInfoOpen' : ''}`} aria-label="Основная навигация">
      <button onClick={() => setInfo('course')}>О КУРСЕ</button>
      <button onClick={() => setInfo('church')}>О ЦЕРКВИ</button>
      <button className="active" onClick={() => go('#/testaments')}>КОНТАКТЫ</button>
    </div>
    {info ? <aside className="landingInfoPanel" aria-live="polite">
      <button className="landingInfoClose" onClick={closeInfo} aria-label="Вернуться в меню">← В меню</button>
      <h2>{info === 'course' ? 'О курсе' : 'О церкви'}</h2>
      {info === 'course' ? <><p>Воскресная школа «Завет Христа» помогает детям знакомиться с Библией понятно, интересно и последовательно.</p><p>Здесь можно изучать уроки, отвечать на вопросы и возвращаться к пройденным материалам.</p></> : <><p>Мы создаём добрые и понятные занятия для детей и семей.</p><p>Изучаем библейские истории вместе, поддерживаем друг друга и растём в вере.</p></>}
    </aside> : <section className="landingExactContent">
      <h1>ВОСКРЕСНАЯ ШКОЛА<br />”ЗАВЕТ ХРИСТА”</h1>
      <div className="landingExactChoices">
        <button onClick={() => go('#/old-testament')}>Ветхий Завет</button>
        <button className="disabled" disabled>Новый Завет</button>
        <button className="disabled" disabled>Трекер успеваемости</button>
      </div>
    </section>}
    <button className="landingBelief" onClick={() => setInfo('course')}>ВО ЧТО МЫ ВЕРИМ</button>
  </main>;
}

function Result({ lesson, result, showAllAnswers, setShowAllAnswers }) { return <><Header /><main className="summary"><button className="backLink" onClick={() => go('#/old-testament/genesis')}>← К урокам</button><div className="celebrate">🎉 🥳 🎉</div><Trophy size={60} className="trophy" /><h1>Ты молодец!</h1><p className="summaryLead">Ты прошёл урок «{lesson.title}» до конца!</p><div className="score"><b>{result.score}</b> из <b>{result.items.length}</b><span> правильных ответов</span></div>{result.items.some((x) => !x.ok) && <section className="mistakes"><h2>Разберём ошибки</h2>{result.items.filter((x) => !x.ok).map((x, i) => <div className="mistake" key={i}><b>{i + 1}. {x.prompt}</b><p>Твой ответ: <span>{x.selectedText}</span></p><p>Правильный ответ: <strong>{x.correctText}</strong></p></div>)}</section>}<button className="showAnswers" onClick={() => setShowAllAnswers((v) => !v)}>{showAllAnswers ? 'Скрыть все ответы' : 'Показать все ответы'}</button>{showAllAnswers && <section className="allAnswers"><h2>Все вопросы и ответы</h2>{result.items.map((x, i) => <div className="answerReview" key={i}><b>{i + 1}. {x.prompt}</b><p>Твой ответ: <span className={x.ok ? 'answerCorrect' : 'answerWrong'}>{x.selectedText}</span></p><p>Правильный ответ: <strong>{x.correctText}</strong></p></div>)}</section>}<button className="check" onClick={() => go('#/old-testament/genesis')}>К урокам</button></main></>; }
function Lesson({ lesson, slide, slideIndex, answers, choose, canNext, finish }) { const total = lesson.slides.length; return <><Header /><main><div className="crumb" onClick={() => go('#/old-testament/genesis')}><Home size={15} /> К урокам <ChevronRight size={15} />{lesson.title}</div><section className="hero"><div><small>УРОК {String(lesson.id).slice(0, 2)} · БЫТИЕ</small><h1>{slide.title || lesson.title}</h1><p>Слайд {slideIndex + 1} из {total}</p></div><div className="progress"><i style={{ width: `${(slideIndex + 1) / total * 100}%` }} /></div></section><div className="media"><div className="video">{slide.video_url ? <iframe src={slide.video_url} title="Видео урока" allow="autoplay" allowFullScreen /> : <div className="missing">🎬<b>Видео пока не добавлено</b></div>}</div><div className="slide">{slide.presentation_embed_url ? <iframe src={`${slide.presentation_embed_url}&rm=minimal`} title="Слайд презентации" /> : <div className="missing">🖼️<b>Слайд пока не добавлен</b></div>}</div></div><section className="questions"><div className="questionHead"><span>💡</span><h2>Вопросы к слайду</h2></div>{(slide.questions || []).map((q, i) => <div className="q" key={i}><label>{q.prompt}</label>{q.options?.map((o, j) => <label className="option" key={j}><input type="radio" name={`q-${slideIndex}-${i}`} checked={answers[`${slideIndex}-${i}`]?.selected === j} onChange={() => choose(q, i, j)} />{o.text}</label>)}</div>)}<button className="check" disabled={!canNext()} onClick={() => slideIndex === total - 1 ? finish() : go(`#/old-testament/genesis/lesson/${lesson.id.split('/')[1].split('.')[0] - 1}/slide/${slideIndex + 1}`)}><CheckCircle size={18} /> {!canNext() ? 'Ответь на все вопросы' : slideIndex === total - 1 ? 'Завершить урок' : 'Сохранить и дальше'}</button><div className="nav"><button disabled={!slideIndex} onClick={() => go(`#/old-testament/genesis/lesson/${lesson.id.split('/')[1].split('.')[0] - 1}/slide/${slideIndex - 1}`)}><ChevronLeft /> Назад</button><button disabled={!canNext()} onClick={() => slideIndex === total - 1 ? finish() : go(`#/old-testament/genesis/lesson/${lesson.id.split('/')[1].split('.')[0] - 1}/slide/${slideIndex + 1}`)}>Следующий <ChevronRight /></button></div></section></main></>; }
function Header() { return <header><button className="siteHeaderHome" onClick={() => go('#/')} aria-label="На главную"><BookOpen /><span><b>Воскресная школа</b><small>«Завет Христа»</small></span></button></header>; }
createRoot(document.getElementById('root')).render(<App />);
