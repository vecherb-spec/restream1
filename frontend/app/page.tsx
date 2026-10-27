import Link from "next/link";
import Image from "next/image";
import { CONTACT_EMAIL, PRODUCT_NAME } from "@/lib/branding";

const steps = [
  {
    title: "Подключите кодер",
    text: "Скопируйте RTMP-сервер и ключ потока в OBS, vMix или аппаратный кодер.",
  },
  {
    title: "Выберите площадки",
    text: "Добавьте YouTube, VK, Rutube, Telegram, Twitch или любой RTMP/RTMPS-адрес.",
  },
  {
    title: "Запустите один эфир",
    text: `${PRODUCT_NAME} принимает входящий поток и одновременно отправляет его во все выбранные направления.`,
  },
];

const features = [
  {
    icon: "↗",
    title: "Один поток на все площадки",
    text: "Отправляйте один RTMP-поток из студии, а сервис раздаст его по выбранным направлениям.",
  },
  {
    icon: "●",
    title: "Статус каждого направления",
    text: "Кабинет показывает, где эфир идет, где идет переподключение и где нужна проверка.",
  },
  {
    icon: "↻",
    title: "Автовосстановление",
    text: "Отдельные FFmpeg-процессы перезапускаются при сетевых сбоях без остановки остальных площадок.",
  },
  {
    icon: "◆",
    title: "Профили площадок",
    text: "Сохраняйте RTMP-адреса и ключи, чтобы не вводить их заново перед каждым эфиром.",
  },
  {
    icon: "▣",
    title: "Предпросмотр сигнала",
    text: "HLS-предпросмотр помогает быстро проверить входящий поток прямо из браузера.",
  },
  {
    icon: "!",
    title: "Оперативные уведомления",
    text: "Получайте сигналы о старте, остановке и проблемах доставки на площадки.",
  },
];

const audience = [
  "команд мероприятий и организаторов конференций",
  "продакшн-студий и технических подрядчиков",
  "блогеров, экспертов и онлайн-школ",
  "компаний, которым нужен единый контроль трансляций",
];

const faqs = [
  {
    question: "Нужно менять OBS или vMix?",
    answer: "Нет. Подойдет любой кодер, который умеет отправлять RTMP-поток.",
  },
  {
    question: "Какие площадки можно подключить?",
    answer: "YouTube, VK, Rutube, Telegram, Twitch и любые пользовательские RTMP/RTMPS-адреса.",
  },
  {
    question: "Что с тарифами?",
    answer: "Тарифы появятся позже. Сейчас продукт готовится к тестовому запуску.",
  },
  {
    question: "Сервис транскодирует видео?",
    answer: "Текущая версия передает поток дальше без изменения качества и параметров кодирования.",
  },
];

const platforms = ["YouTube", "VK Видео", "Rutube", "Telegram", "Twitch", "RTMP"];

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
          <span className="eyebrow">Рестриминг для OBS и vMix</span>
          <div className="hero-badges" aria-label="Ключевые возможности">
            <span>OBS / vMix</span>
            <span>RTMP / RTMPS</span>
            <span>Соцсети и RTMP</span>
          </div>
          <h1 className="hero-title">
            Один эфир —
            <span> все площадки сразу</span>
          </h1>
          <p>
            Подключите OBS, vMix или аппаратный кодер один раз. Medialive Restream доставит
            поток на нужные платформы и покажет состояние каждого направления в одном кабинете.
          </p>
          <div className="hero-actions">
            <Link className="button" href="/dashboard">
              Начать трансляцию
            </Link>
            <Link className="button secondary" href="/dashboard">
              Войти в кабинет
            </Link>
          </div>
          <p className="hero-note">Тестовая версия. Тарифы и публичная регистрация будут объявлены отдельно.</p>
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
              <span className="mockup-live">LIVE</span>
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
              <span>RTMP server</span>
              <strong>rtmp://.../live</strong>
            </div>
            <button>Скопировать ключ</button>
          </div>
        </div>
      </section>

      <section className="platform-strip" aria-label="Поддерживаемые площадки">
        <span>Транслируйте одновременно:</span>
        <div>
          {platforms.map((platform) => (
            <strong key={platform}>{platform}</strong>
          ))}
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
          <span className="eyebrow">Возможности MVP</span>
          <h2>Контроль доставки без лишней операционной рутины</h2>
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
            Продукт подходит для регулярных эфиров, мероприятий, запусков, конференций,
            онлайн-курсов и студийного производства. Главное — один стабильный входящий
            поток и понятный контроль исходящих направлений.
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

      <section className="landing-section pricing-teaser">
        <div>
          <span className="eyebrow">Тарифы</span>
          <h2>Пакеты для beta будут объявлены отдельно</h2>
          <p>
            Сейчас важно проверить стабильность входящего потока, доставку на площадки
            и удобство кабинета. Коммерческие условия появятся после beta-теста.
          </p>
        </div>
        <Link className="button" href="/dashboard">
          Начать трансляцию
        </Link>
      </section>

      <section className="landing-section contact-section" id="contact">
        <div>
          <span className="eyebrow">Контакт</span>
          <h2>Хотите протестировать одновременную трансляцию?</h2>
          <p>
            Напишите, какие площадки нужны, какой кодер используете и какой ожидается битрейт.
            Подскажем, как подготовить тестовый запуск.
          </p>
        </div>
        <div className="contact-actions">
          <a className="button" href={`mailto:${CONTACT_EMAIL}`}>
            {CONTACT_EMAIL}
          </a>
          <Link className="button secondary" href="/dashboard">
            Личный кабинет
          </Link>
        </div>
      </section>

      <footer className="landing-footer">
        <Image src="/logo.png" alt={PRODUCT_NAME} width={254} height={30} />
        <span>Рестриминг на несколько площадок</span>
      </footer>
    </main>
  );
}
