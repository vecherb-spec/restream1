import Link from "next/link";
import Image from "next/image";
import { CONTACT_EMAIL, PRODUCT_NAME } from "@/lib/branding";

const steps = [
  {
    title: "Подключите OBS или vMix",
    text: "Скопируйте адрес сервера и ключ из личного кабинета.",
  },
  {
    title: "Добавьте площадки",
    text: "Укажите ключи YouTube, VK, Rutube, Telegram или любого RTMP-сервера.",
  },
  {
    title: "Начните трансляцию",
    text: "Нажмите «Начать трансляцию» в OBS или vMix, дальше эфир сам уйдёт на все площадки.",
  },
];

const features = [
  {
    icon: "↗",
    title: "Один поток на все площадки",
    text: "Интернет нагружается как при одной трансляции.",
  },
  {
    icon: "●",
    title: "Статус каждой площадки",
    text: "Сразу видно, где идёт эфир, а где ошибка.",
  },
  {
    icon: "↻",
    title: "Автовосстановление",
    text: "Если площадка отвалилась, сервис подключит её заново, а остальные продолжат работать.",
  },
  {
    icon: "◆",
    title: "Профили площадок",
    text: "Ключи сохраняются, вводить их перед каждым эфиром не нужно.",
  },
  {
    icon: "!",
    title: "Уведомления в Telegram",
    text: "Сообщим о старте, остановке и сбоях.",
  },
  {
    icon: "⟲",
    title: "Поддержка SRT",
    text: "Можно подключаться не только по RTMP, но и по SRT, он надёжнее на слабом интернете.",
  },
];

const faqs = [
  {
    question: "Подойдёт моя программа для эфира?",
    answer:
      "Да, подойдёт любая, которая умеет отправлять поток по RTMP или SRT: OBS, vMix и другие.",
  },
  {
    question: "Какие площадки можно подключить?",
    answer: "YouTube, VK, Rutube, Telegram, Twitch и любую площадку, которая выдаёт адрес и ключ.",
  },
  {
    question: "Качество картинки изменится?",
    answer: "Нет, на площадки уходит то же видео, что вы отправили.",
  },
];

export default function LandingPage() {
  return (
    <main className="landing-page">
      <header className="landing-nav">
        <Link className="landing-brand" href="/">
          <Image src="/logo.png" alt={PRODUCT_NAME} width={287} height={34} priority />
        </Link>
      </header>

      <section className="landing-hero">
        <div className="hero-copy">
          <h1 className="hero-title">
            Один эфир —
            <span> все площадки сразу</span>
          </h1>
          <p>
            Отправьте один поток из OBS или vMix, а мы одновременно выведем его на YouTube, VK, Rutube,
            Telegram и другие площадки.
          </p>
          <div className="hero-actions">
            <Link className="button" href="/dashboard">
              Начать трансляцию
            </Link>
            <Link className="button secondary" href="/dashboard">
              Войти
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
              <span>Сервер</span>
              <strong>rtmp://.../live</strong>
            </div>
            <button type="button">Скопировать ключ</button>
          </div>
        </div>
      </section>

      <section className="landing-section" id="how">
        <h2>Как это работает</h2>
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
        <h2>Всё под контролем в одном кабинете</h2>
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

      <section className="landing-section" id="audience">
        <h2>Для кого</h2>
        <p>
          Для церквей и религиозных общин, организаторов мероприятий, продакшн-студий, блогеров,
          онлайн-школ и компаний.
        </p>
      </section>

      <section className="landing-section" id="faq">
        <h2>Частые вопросы</h2>
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
        <h2>Контакты</h2>
        <div className="contact-actions">
          <a className="button" href={`mailto:${CONTACT_EMAIL}`}>
            info@medialive.ru
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
