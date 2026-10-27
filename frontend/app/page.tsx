import Link from "next/link";
import Image from "next/image";
import { CONTACT_EMAIL, PRODUCT_NAME } from "@/lib/branding";

const steps = [
  {
    title: "Возьмите данные из кабинета",
    text: "Там же адрес сервера и ключ потока — вставьте их в настройки вещания.",
  },
  {
    title: "Включите площадки",
    text: "YouTube, VK Видео, Rutube, Telegram, Twitch или свой канал по адресу и ключу.",
  },
  {
    title: "Запустите эфир",
    text: "Запустили вещание в студии — раздача на выбранные каналы пойдёт сама.",
  },
];

const features = [
  {
    icon: "↗",
    title: "Один поток на все площадки",
    text: "Включили каналы в кабинете — один выход из студии, все получатели сразу.",
  },
  {
    icon: "●",
    title: "Статус каждой площадки",
    text: "Видно, кто в эфире, кто переподключается и где сбой.",
  },
  {
    icon: "↻",
    title: "Автовосстановление",
    text: "Отвалилась одна площадка — попробуем вернуть её, остальные не прервутся.",
  },
  {
    icon: "◆",
    title: "Профили площадок",
    text: "Сохраните URL и ключи — перед следующим эфиром не вводите заново.",
  },
  {
    icon: "!",
    title: "Уведомления в Telegram",
    text: "Старт, стоп и сбои — сообщением в Telegram.",
  },
  {
    icon: "⟲",
    title: "Поддержка SRT и NDI",
    text: "Вход уже есть; SRT и NDI добавим следующими.",
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
    question: "Можно оставить свой софт для вещания?",
    answer: "Да. Главное — уметь отправить поток на адрес и ключ из кабинета.",
  },
  {
    question: "Какие площадки можно подключить?",
    answer: "Популярные соцсети и видеохостинги плюс свои RTMP- или SRT-адреса.",
  },
  {
    question: "Вы перекодируете картинку?",
    answer: "Нет — параметры и качество остаются такими, как отдаёт ваша студия.",
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
            <span>Много площадок</span>
            <span>Один сигнал</span>
            <span>Статус в кабинете</span>
          </div>
          <h1 className="hero-title">
            Один эфир —
            <span> все площадки сразу</span>
          </h1>
          <p>
            Подключите OBS или vMix один раз — эфир сам уйдёт на YouTube, VK, Telegram и другие
            площадки.
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
              <span>Сервер</span>
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
          <h2>Когда эфир нужен не на одной площадке</h2>
          <p>
            Богослужения, конференции, вебинары и корпоративные трансляции — один запуск, все каналы
            на виду.
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
          <h2>Частые вопросы</h2>
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
