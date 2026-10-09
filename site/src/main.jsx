import React, { useEffect, useState } from 'react';
import { createRoot } from 'react-dom/client';
import { ChevronLeft, ChevronRight, CheckCircle, Trophy, LockKeyhole } from 'lucide-react';
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

const NAV = [['О курсе', 'course'], ['О церкви', 'church'], ['Во что мы верим', 'beliefs'], ['Контакты', 'contacts']];
const INFO_TITLE = { course: 'О курсе', church: 'О церкви', beliefs: 'Во что мы верим', contacts: 'Контакты' };
const INFO = {
  course: (
    <>
      <p>Воскресная школа «Завет Христа» помогает детям знакомиться с Библией понятно, интересно и последовательно.</p>
      <p>Здесь можно изучать уроки, отвечать на вопросы и возвращаться к пройденным материалам.</p>
    </>
  ),
  church: (
    <>
      <p>Мы создаём добрые и понятные занятия для детей и семей.</p>
      <p>Изучаем библейские истории вместе, поддерживаем друг друга и растём в вере.</p>
    </>
  ),
  beliefs: (
    <>
      <p>Мы верим, что Библия — Слово Божье, и изучаем её последовательно, от книги к книге.</p>
      <p>В центре всей Библии — Иисус Христос и Божий Завет с Его народом.</p>
    </>
  ),
  contacts: <p>Свяжитесь с нами, чтобы узнать расписание воскресной школы и присоединиться к занятиям.</p>,
};

function App() {
  const [data, setData] = useState([]);
  const [route, setRoute] = useState(parseRoute);
  const [answers, setAnswers] = useState({});
  const [result, setResult] = useState(null);
  const [showAllAnswers, setShowAllAnswers] = useState(false);
  useEffect(() => { fetch(`${base}generated/course.json`).then((r) => r.json()).then(setData); }, []);
  useEffect(() => { const sync = () => setRoute(parseRoute()); window.addEventListener('hashchange', sync); return () => window.removeEventListener('hashchange', sync); }, []);
  useEffect(() => { window.scrollTo({ top: 0, left: 0, behavior: 'instant' }); }, [route.page, route.lesson, route.slide]);
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
    const saved = readProgress();
    const savedResult = saved[progressKey]?.result ?? null;
    saved[progressKey] = { slide: slideIndex, answers, result: result ?? savedResult };
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
  if (route.page === 'testaments') return (
    <main className="page pagePlain">
      <button className="backPill" onClick={() => go('#/')}>← На главную</button>
      <header className="pageHead">
        <h1 className="pageTitle">Выбери Завет</h1>
        <p className="pageSub">С какой части Библии начнём путешествие?</p>
      </header>
      <div className="choiceGrid">
        <button className="choiceCard" onClick={() => go('#/old-testament')}><span>📜</span><strong>Ветхий Завет</strong><small>Истории от Сотворения мира</small></button>
        <button className="choiceCard disabled" disabled><span>✝️</span><strong>Новый Завет</strong><small>Скоро будет доступен</small><LockKeyhole /></button>
      </div>
    </main>
  );
  if (route.page === 'books') return (
    <main className="page pageOt">
      <button className="backPill" onClick={() => go('#/testaments')}>← К Заветам</button>
      <header className="pageHead">
        <h1 className="pageTitle">Ветхий Завет</h1>
        <p className="pageSub">От сотворения до Рождества</p>
      </header>
      <div className="grid">
        <BookCards />
      </div>
      <div className="boyOverlay" aria-hidden="true" />
    </main>
  );
  if (!lesson && (route.page === 'lessons' || route.page === 'lesson')) return (
    <main className="page pageOt">
      <button className="backPill" onClick={() => go('#/old-testament')}>← К книгам</button>
      <header className="pageHead">
        <h1 className="pageTitle">Книга Бытие</h1>
        <p className="pageSub">Выбери урок</p>
      </header>
      <div className="grid">
        {lessons.map((x, i) => (
          <button className={`card ${!x.slides.length ? 'disabled' : ''}`} key={x.id} disabled={!x.slides.length} onClick={() => openLesson(i)}>
            <span className="cardBadge">{String(i + 1).padStart(2, '0')}</span>
            <em className="cardRef">{x.ref}</em>
            <strong className="cardTitle">{x.title}</strong>
            <small className="cardSub">{x.slides.length ? `${x.slides.length} слайдов` : 'Материалы готовятся'}</small>
          </button>
        ))}
      </div>
      <div className="boyOverlay" aria-hidden="true" />
    </main>
  );
  if (route.page === 'result' && result) return <Result lesson={lesson} result={result} showAllAnswers={showAllAnswers} setShowAllAnswers={setShowAllAnswers} />;
  if (!lesson || !slide) return null;
  return <Lesson lesson={lesson} lessonIndex={route.lesson} slide={slide} slideIndex={slideIndex} answers={answers} choose={choose} canNext={canNext} finish={finish} />;
}

function BookCards() {
  return (
    <>
      <button className="card" onClick={() => go('#/old-testament/genesis')}>
        <span className="cardBadge">01</span>
        <strong className="cardTitle">Бытие</strong>
        <small className="cardSub">История начала всего</small>
      </button>
      {['Исход', 'Левит', 'Числа', 'Второзаконие'].map((name, i) => (
        <button className="card disabled" disabled key={name}>
          <span className="cardBadge">{String(i + 2).padStart(2, '0')}</span>
          <strong className="cardTitle">{name}</strong>
          <small className="cardSub">Материалы готовятся</small>
          <LockKeyhole className="lock" />
        </button>
      ))}
    </>
  );
}

function Landing() {
  const [info, setInfo] = useState(null);
  const closeInfo = () => setInfo(null);
  const activeKey = info ?? 'beliefs';
  return (
    <main className="home">
      <nav className="homeNav" aria-label="Основная навигация">
        {NAV.map(([label, key]) => (
          <button key={key} className={`pill ${activeKey === key ? 'active' : ''}`} onClick={() => setInfo(info === key ? null : key)}>{label}</button>
        ))}
      </nav>
      {info ? (
        <aside className="infoPanel" aria-live="polite">
          <button className="infoClose" onClick={closeInfo} aria-label="Вернуться в меню">← В меню</button>
          <h2>{INFO_TITLE[info]}</h2>
          {INFO[info]}
        </aside>
      ) : (
        <section className="homeContent">
          <h1 className="homeTitle"><span>Воскресная школа</span><span>“Завет Христа”</span></h1>
          <div className="homeCards">
            <button className="homeCard" onClick={() => go('#/old-testament')}>Ветхий Завет</button>
            <button className="homeCard disabled" disabled>Новый Завет</button>
            <button className="homeCard disabled" disabled>Трекер успеваемости</button>
          </div>
          <div className="homeMobileBooks"><BookCards /></div>
        </section>
      )}
    </main>
  );
}

function Result({ lesson, result, showAllAnswers, setShowAllAnswers }) {
  return (
    <main className="page pageOt summaryPage">
      <button className="backPill" onClick={() => go('#/old-testament/genesis')}>← К урокам</button>
      <div className="celebrate">🎉 🥳 🎉</div>
      <Trophy size={60} className="trophy" />
      <h1>Ты молодец!</h1>
      <p className="summaryLead">Ты прошёл урок «{lesson.title}» до конца!</p>
      <div className="score"><b>{result.score}</b> из <b>{result.items.length}</b><span> правильных ответов</span></div>
      {result.items.some((x) => !x.ok) && (
        <section className="mistakes">
          <h2>Разберём ошибки</h2>
          {result.items.filter((x) => !x.ok).map((x, i) => (
            <div className="mistake" key={i}><b>{i + 1}. {x.prompt}</b><p>Твой ответ: <span>{x.selectedText}</span></p><p>Правильный ответ: <strong>{x.correctText}</strong></p></div>
          ))}
        </section>
      )}
      <button className="showAnswers" onClick={() => setShowAllAnswers((v) => !v)}>{showAllAnswers ? 'Скрыть все ответы' : 'Показать все ответы'}</button>
      {showAllAnswers && (
        <section className="allAnswers">
          <h2>Все вопросы и ответы</h2>
          {result.items.map((x, i) => (
            <div className="answerReview" key={i}><b>{i + 1}. {x.prompt}</b><p>Твой ответ: <span className={x.ok ? 'answerCorrect' : 'answerWrong'}>{x.selectedText}</span></p><p>Правильный ответ: <strong>{x.correctText}</strong></p></div>
          ))}
        </section>
      )}
      <button className="check" onClick={() => go('#/old-testament/genesis')}>К урокам</button>
    </main>
  );
}

function Lesson({ lesson, lessonIndex, slide, slideIndex, answers, choose, canNext, finish }) {
  const total = lesson.slides.length;
  const goSlide = (i) => go(`#/old-testament/genesis/lesson/${lessonIndex}/slide/${i}`);
  return (
    <main className="page pageOt lessonPage">
      <section className="lessonHead">
        <div className="lessonHeadLeft">
          <button className="backPill" onClick={() => go('#/old-testament/genesis')}>← К урокам</button>
          <small className="lessonCrumb">УРОК {String(lessonIndex + 1).padStart(2, '0')} · {lesson.ref || 'БЫТИЕ'}</small>
        </div>
        <div className="lessonTitleBlock">
          <h1 className="lessonTitle">{slide.title || lesson.title}</h1>
          <p className="lessonMeta">Слайд {slideIndex + 1} из {total}</p>
        </div>
        <div className="lessonHeadRight">
          <div className="progress"><i style={{ width: `${(slideIndex + 1) / total * 100}%` }} /></div>
        </div>
      </section>
      <div className="media">
        <div className="frame">
          {slide.video_url ? <iframe src={slide.video_url} title="Видео урока" allow="autoplay" allowFullScreen /> : <div className="missing">🎬<b>Видео пока не добавлено</b></div>}
        </div>
        <div className="frame">
          {slide.presentation_embed_url ? <iframe src={`${slide.presentation_embed_url}&rm=minimal`} title="Слайд презентации" /> : <div className="missing">🖼️<b>Слайд пока не добавлен</b></div>}
        </div>
      </div>
      <section className="questions">
        <div className="questionHead"><h2>Вопросы к слайду</h2></div>
        {(slide.questions || []).map((q, i) => (
          <div className="q" key={i}>
            <label>{q.prompt}</label>
            {q.options?.map((o, j) => (
              <label className="option" key={j}><input type="radio" name={`q-${slideIndex}-${i}`} checked={answers[`${slideIndex}-${i}`]?.selected === j} onChange={() => choose(q, i, j)} />{o.text}</label>
            ))}
          </div>
        ))}
        <button className="check" disabled={!canNext()} onClick={() => slideIndex === total - 1 ? finish() : goSlide(slideIndex + 1)}>
          <CheckCircle size={18} /> {!canNext() ? 'Ответь на все вопросы' : slideIndex === total - 1 ? 'Завершить урок' : 'Сохранить и дальше'}
        </button>
        <div className="nav">
          <button disabled={!slideIndex} onClick={() => goSlide(slideIndex - 1)}><ChevronLeft size={18} /> Назад</button>
          <button disabled={!canNext()} onClick={() => slideIndex === total - 1 ? finish() : goSlide(slideIndex + 1)}>Следующий <ChevronRight size={18} /></button>
        </div>
      </section>
    </main>
  );
}

createRoot(document.getElementById('root')).render(<App />);
