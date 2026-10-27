import Link from "next/link";
import Image from "next/image";
import { CONTACT_EMAIL, PRODUCT_NAME } from "@/lib/branding";

const steps = [
  {
    title: "Подключите кодер",
    text: "Возьмите RTMP-сервер и ключ в кабинете и вставьте их в OBS, vMix или аппаратный кодер.",
  },
  {
    title: "Выберите площадки",
    text: "Добавьте YouTube, VK Видео, Rutube, Telegram, Twitch, RTMP или SRT.",
  },
  {
    title: "Запустите эфир",
    text: `${PRODUCT_NAME} примет поток и отправит его на выбранные площадки.`,
  },
];

const features = [
  {
    icon: "↗",
    title: "Один поток на все площадки",
    text: "Проведите эфир один раз. Мы отправим поток на выбранные площадки.",
  },
  {
    icon: "●",
    title: "Статус каждой площадки",
    text: "В кабинете видно, где эфир идёт, где идёт переподключение и где нужна помощь.",
  },
  {
    icon: "↻",
    title: "Автовосстановление",
    text: "Если одна площадка отвалилась, сервис попробует подключить её снова. Остальные продолжат вещать.",
  },
  {
    icon: "◆",
    title: "Профили площадок",
    text: "Сохраните адреса и ключи один раз. Перед следующим эфиром их не придётся вводить заново.",
  },
  {
    icon: "!",
    title: "Уведомления в Telegram",
    text: "Сообщим о старте, остановке и проблемах на площадках.",
  },
  {
    icon: "⟲",
    title: "Поддержка SRT и NDI",
    text: "RTMP уже работает. SRT и NDI запланированы как следующие варианты входа.",
  },
];

const audience = [
  "церкви и религиозные общины",
  "организаторы мероприятий и конференций",
  "продакшн-студии",
  "блогеры и онлайн-школы",
  "компании",
];

const faqs = [
  {
    question: "Можно оставить OBS или vMix?",
    answer: "Нет. Подойдёт любой кодер, который отправляет RTMP-поток.",
  },
  {
    question: "Какие площадки можно подключить?",
    answer: "YouTube, VK Видео, Rutube, Telegram, Twitch и свои RTMP/SRT-адреса.",
  },
  {
    question: "Сервис меняет качество видео?",
    answer: "Пока нет. Сейчас поток передаётся дальше без изменения качества и параметров кодирования.",
  },
];

export default function LandingPage() {
  return (
    <main className="landing-page">
      <header className="landing-nav">
        <Link className="landing-brand" href="/">
          <Image src="/logo.png" alt={PRODUCT_NAME} width={287} height={34} priority />
        </Link>
        <nav>
          <a href="#how">Как работает</a>
          <a href="#features">Возможности</a>
          <a href="#faq">Вопросы</a>
        </nav>
        <Link className="button secondary landing-login" href="/dashboard">
          Войти в личный кабинет
        </Link>
      </header>

      <section className="landing-hero">
        <div className="hero-copy">
          <div className="hero-badges" aria-label="Ключевые возможности">
            <span>OBS / vMix</span>
            <span>RTMP / SRT</span>
            <span>Соцсети и RTMP</span>
          </div>
          <h1 className="hero-title">
            Один эфир —
            <span> все площадки сразу</span>
          </h1>
          <p>
            Проведите эфир один раз. Medialive Restream примет поток из OBS, vMix
            или аппаратного кодера и отправит его на YouTube, VK Видео, Rutube, Telegram, Twitch и RTMP-площадки.
            Статус каждой площадки видно в кабинете.
          </p>
          <div className="hero-actions">
            <Link className="button" href="/dashboard">
              Начать трансляцию
            </Link>
            <Link className="button secondary" href="/dashboard">
              Войти в кабинет
            </Link>
          </div>
        </div>
        <div className="dashboard-mockup" aria-label="Макет личного кабинета">
          <div className="mockup-topbar">
            <span className="mockup-dot red" />
            <span className="mockup-dot yellow" />
            <span className="mockup-dot green" />
            <strong>{PRODUCT_NAME}</strong>
          </div>
          <div className="mockup-grid">
            <div className="mockup-preview">
              <span className="mockup-live">ЭФИР</span>
              <div className="mockup-play">▶</div>
              <p>Основная трансляция</p>
            </div>
            <div className="mockup-side">
              {["YouTube", "VK Видео", "Rutube", "Telegram", "Twitch"].map((platform, index) => (
                <div className="mockup-platform" key={platform}>
                  <span className={index === 2 ? "status-dot yellow" : "status-dot green"} />
                  <div>
                    <strong>{platform}</strong>
                    <small>{index === 2 ? "Переподключение" : "В эфире"}</small>
                  </div>
                </div>
              ))}
            </div>
          </div>
          <div className="mockup-footer">
            <div>
              <span>RTMP-сервер</span>
              <strong>rtmp://.../live</strong>
            </div>
            <button>Скопировать ключ</button>
          </div>
        </div>
      </section>

      <section className="landing-section" id="how">
        <div className="section-heading">
          <span className="eyebrow">Как это работает</span>
          <h2>Три шага до эфира</h2>
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
          <span className="eyebrow">Возможности</span>
          <h2>Всё под контролем в одном кабинете</h2>
        </div>
        <div className="feature-list">
          {features.map((feature) => (
            <div className="feature-item" key={feature.title}>
              <span>{feature.icon}</span>
              <div>
                <h3>{feature.title}</h3>
                <p>{feature.text}</p>
              </div>
            </div>
          ))}
        </div>
      </section>

      <section className="landing-section split">
        <div>
          <span className="eyebrow">Для кого</span>
          <h2>Для команд, которым нужно вещать сразу на несколько платформ</h2>
          <p>
            Подходит для регулярных богослужений, мероприятий, конференций и онлайн-курсов.
            Один поток. Один кабинет. Все площадки перед глазами.
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
          <span className="eyebrow">Вопросы</span>
          <h2>Коротко о запуске и ограничениях</h2>
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
          <h2>Контакты</h2>
        </div>
        <div className="contact-actions">
          <a className="button" href={`mailto:${CONTACT_EMAIL}`}>
            {CONTACT_EMAIL}
          </a>
          <a className="button secondary" href="https://medialive.tech" target="_blank" rel="noopener noreferrer">
            medialive.tech
          </a>
        </div>
      </section>

      <footer className="landing-footer">
        <Image src="/logo.png" alt={PRODUCT_NAME} width={254} height={30} />
      </footer>
    </main>
  );
}
