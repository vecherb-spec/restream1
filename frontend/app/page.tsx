import Link from "next/link";

const steps = [
  {
    title: "Подключаете OBS или vMix",
    text: "В личном кабинете копируете RTMP server и stream key, вставляете их в привычный видеокодер.",
  },
  {
    title: "Выбираете площадки",
    text: "Добавляете YouTube, VK, Rutube, Telegram или свой RTMP endpoint и включаете нужные направления.",
  },
  {
    title: "Ведете один эфир",
    text: "Medialive restream принимает один поток и отправляет его на выбранные площадки одновременно.",
  },
];

const features = [
  "Один входящий поток вместо нескольких нагрузок на компьютер",
  "Отдельный статус по каждой площадке: видно, где эфир идет, а где нужна проверка",
  "Автоперезапуск FFmpeg worker при сетевых сбоях",
  "Stream key хранится в личном кабинете и может быть перевыпущен",
  "HLS-preview для проверки эфира из браузера",
  "Telegram-уведомления о старте, остановке и проблемах доставки",
];

const audience = [
  "церквей с небольшой медиакомандой",
  "волонтеров, которые запускают эфиры по воскресеньям и на событиях",
  "служений, которым нужно вещать сразу в YouTube, VK, Rutube и Telegram",
  "команд, которым важны простые инструкции и понятный статус трансляции",
];

const faqs = [
  {
    question: "Нужен ли новый видеокодер?",
    answer: "Нет. Можно использовать OBS, vMix или другой кодер, который умеет отдавать RTMP-поток.",
  },
  {
    question: "Можно ли подключить Telegram?",
    answer: "Да, если у вас есть RTMP endpoint для трансляции в Telegram. Уведомления в Telegram настраиваются отдельно.",
  },
  {
    question: "Что с тарифами?",
    answer: "Тарифы скоро появятся. Сейчас проект готовится к тестированию, поэтому условия лучше уточнить у Medialive.",
  },
  {
    question: "Это заменяет YouTube/VK/Rutube?",
    answer: "Нет. Сервис помогает отправить один входящий поток сразу на ваши существующие площадки.",
  },
];

export default function LandingPage() {
  return (
    <main className="landing-page">
      <header className="landing-nav">
        <Link className="landing-brand" href="/">
          <span className="brand-mark">M</span>
          <span>Medialive restream</span>
        </Link>
        <nav>
          <a href="#how">Как работает</a>
          <a href="#features">Возможности</a>
          <a href="#faq">FAQ</a>
        </nav>
        <Link className="button secondary landing-login" href="/dashboard">
          Войти в личный кабинет
        </Link>
      </header>

      <section className="landing-hero">
        <div className="hero-copy">
          <span className="eyebrow">Для церковных онлайн-трансляций</span>
          <h1>Один эфир из OBS или vMix — сразу на YouTube, VK, Rutube и Telegram</h1>
          <p>
            Простая restream-платформа для церквей и медиаволонтеров: меньше настроек,
            понятный кабинет и контроль доставки на каждую площадку.
          </p>
          <div className="hero-actions">
            <Link className="button" href="/dashboard">
              Войти в личный кабинет
            </Link>
            <a className="button secondary" href="#contact">
              Связаться с Medialive
            </a>
          </div>
          <p className="hero-note">Сейчас сервис готовится к тестированию. Цены и пакеты — скоро.</p>
        </div>
        <div className="hero-card" aria-label="Схема рестрима">
          <div className="signal-card source">OBS / vMix</div>
          <div className="signal-line" />
          <div className="signal-card core">Medialive restream</div>
          <div className="destinations-cloud">
            <span>YouTube</span>
            <span>VK</span>
            <span>Rutube</span>
            <span>Telegram</span>
          </div>
        </div>
      </section>

      <section className="landing-section" id="how">
        <div className="section-heading">
          <span className="eyebrow">Как это работает</span>
          <h2>Три шага перед началом служения</h2>
        </div>
        <div className="landing-grid three">
          {steps.map((step, index) => (
            <article className="landing-card" key={step.title}>
              <span className="step-number">{index + 1}</span>
              <h3>{step.title}</h3>
              <p>{step.text}</p>
            </article>
          ))}
        </div>
      </section>

      <section className="landing-section" id="features">
        <div className="section-heading">
          <span className="eyebrow">Возможности MVP</span>
          <h2>Все важное для первых тестовых эфиров</h2>
        </div>
        <div className="feature-list">
          {features.map((feature) => (
            <div className="feature-item" key={feature}>
              <span>✓</span>
              <p>{feature}</p>
            </div>
          ))}
        </div>
      </section>

      <section className="landing-section split">
        <div>
          <span className="eyebrow">Для кого</span>
          <h2>Для церквей, где эфир запускают реальные люди, а не штат инженеров</h2>
          <p>
            Интерфейс сделан так, чтобы медиаволонтер видел главное: куда идет эфир,
            какой stream key использовать и какая площадка требует внимания.
          </p>
        </div>
        <ul className="audience-list">
          {audience.map((item) => (
            <li key={item}>{item}</li>
          ))}
        </ul>
      </section>

      <section className="landing-section" id="faq">
        <div className="section-heading">
          <span className="eyebrow">FAQ</span>
          <h2>Коротко о запуске</h2>
        </div>
        <div className="faq-list">
          {faqs.map((faq) => (
            <article className="faq-item" key={faq.question}>
              <h3>{faq.question}</h3>
              <p>{faq.answer}</p>
            </article>
          ))}
        </div>
      </section>

      <section className="landing-section contact-section" id="contact">
        <div>
          <span className="eyebrow">Контакт</span>
          <h2>Хотите протестировать restream для вашей церкви?</h2>
          <p>
            Напишите Medialive: расскажите, какие площадки используете и каким кодером ведете эфир.
          </p>
        </div>
        <div className="contact-actions">
          <a className="button" href="mailto:info@medialive.ru">
            info@medialive.ru
          </a>
          <Link className="button secondary" href="/dashboard">
            Личный кабинет
          </Link>
        </div>
      </section>

      <footer className="landing-footer">
        <span>Medialive restream</span>
        <span>Сделано Medialive для церковных трансляций</span>
      </footer>
    </main>
  );
}
